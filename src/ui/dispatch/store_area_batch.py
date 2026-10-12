# -*- coding: utf-8 -*-
"""存储区**框选批量动作**（一键开启 / 一键关闭 / 一键改模式）。

从 `store_area_ops` 拆出（该文件在 R183 新增后越过 500 行上限；按项目纪律
"新逻辑落新模块"，语义归属不变 —— 仍由 `AREA_HANDLERS` 装配到面板上）。

★ P0-1（审计⑦B 实锤）的核心修法就在这里：`_deactivate_or_release_inactive` 是
"单卡关闭"的**三态分流**单一收口 ——
  · `_scene_is_active is False`（明确没在播）→ 只收该卡输出（`_release_inactive_only`），
    **不清编辑层**（原实现无条件走完整释放 → `_clear_editor_layers` 在"没有别的
    场景亮着"时调 `release_all_editor()`，把操作员手调的现场光当场灭掉）；
  · `True`（激活）/ `None`（无从判定）→ 走完整 `_deactivate_scene`（保守方向）。

判据与 `scene_release._release_before_delete` 逐字同源（"删除未激活卡"用的是
同一条分流逻辑），故单卡关闭与框选关闭、删除三条路径语义一致。
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def _selected_or_band_ids(panel) -> list:
    """当前作用对象：优先框选命中，其次已有选中集。"""
    try:
        band = list(panel.list.band_selected_ids() or [])
        if band:
            return band
    except Exception:
        _log.debug("读取框选集失败", exc_info=True)
    try:
        return [str(x) for x in (panel.list.selected_ids() or [])]
    except Exception:
        return []


def _on_area_call(self) -> None:
    """框选/多选 → **一键开启**：把选中的场景全部调用。"""
    from .store_area_ops import (_cache_or_none, _fade_in_scene,
                                 _note_scene_activation)
    ids = _selected_or_band_ids(self)
    if not ids:
        self._status.setText("先在保存区框选或用 Ctrl 多选若干场景")
        return
    # ★ 淡入与模式无关、也与"单个/批量"无关（用户 2026-09-14）：每个场景各自登记，
    #   FadeRunner 按场景记账 → 批量开启时互不干扰（深查 H2/H3）。
    for cid in ids:
        _fade_in_scene(self, _cache_or_none(cid))
    self._recall_ids(ids)
    for cid in ids:                      # 依次恢复性缓存：登记激活顺序（问题7）
        _note_scene_activation(self, cid)
    try:
        active = set(self.list.active_ids() or ())
        active.update(ids)
        self.list.set_active(active)
    except Exception:
        _log.debug("批量开启后置高亮失败", exc_info=True)
    self._status.setText(f"已一键开启 {len(ids)} 个场景")


def _on_area_off(self) -> None:
    """框选/多选 → **一键关闭**（逐卡做三态分流，见模块头 P0-1 说明）。"""
    ids = _selected_or_band_ids(self)
    if not ids:
        self._status.setText("先在保存区框选或用 Ctrl 多选若干场景")
        return
    for cid in ids:
        _deactivate_or_release_inactive(self, cid)
    self._status.setText(f"已一键关闭 {len(ids)} 个场景")


def _deactivate_or_release_inactive(panel, cid: str) -> bool:
    """单卡关闭的**三态分流**（P0-1 单一收口）：返回是否走了"真释放"。

    判据与 `scene_release._release_before_delete` 逐字同源：
      · `_scene_is_active is False`（明确没在播）→ 只收该卡输出，**不清编辑层**；
      · `True` / `None`（激活 / 无从判定）→ 走完整 `_deactivate_scene`（保守方向）。
    """
    from .scene_release import (_deactivate_scene, _release_inactive_only,
                                _scene_is_active)
    cid = str(cid or "")
    if not cid:
        return False
    if _scene_is_active(panel, cid) is False:
        _release_inactive_only(panel, cid)
        return False
    _deactivate_scene(panel, cid)
    return True


def _on_area_mode(self, mode: str) -> None:
    """框选/多选 → **一键设置触发模式**。"""
    ids = _selected_or_band_ids(self)
    if not ids:
        self._status.setText("先在保存区框选或用 Ctrl 多选若干场景")
        return
    try:
        from ...processing.dispatch import get_effect_cache_store
        store = get_effect_cache_store()
        for cid in ids:
            store.set_mode(cid, mode)
    except Exception:
        _log.debug("批量改触发模式失败", exc_info=True)
    self.refresh_saved()
    self.cacheChanged.emit()
    self._status.setText(
        f"已把 {len(ids)} 个场景设为 {_MODE_LABELS.get(mode, mode)}")


_MODE_LABELS = {"latch": "自锁 Latch（点一下保持、再点关闭）",
                "flash": "点动 Flash（按住才应用、松手释放）"}


def _on_area_bind_key(self) -> None:
    """框选/多选 → **都绑到同一个键**（用户 2026-09-14："支持框选一批 scene 去同时
    映射多个效果到同一按键"）。"""
    from .store_area_ops import _after_area_batch, _store_or_none
    ids = _selected_or_band_ids(self)
    if not ids:
        self._status.setText("先在保存区框选或用 Ctrl 多选若干场景")
        return
    # 复用面板上的按键捕获对话框（与单场景绑定同一条 UI 路径）
    cap = getattr(self, "_capture_key", None)
    key = cap(None) if callable(cap) else None
    if key is None:
        return
    store = _store_or_none()
    if store is not None:
        for cid in ids:
            try:
                store.set_key_binding(cid, key)
            except Exception:
                _log.debug("批量绑键失败 cid=%s", cid, exc_info=True)
    _after_area_batch(self, f"已把 {len(ids)} 个场景绑定到按键 {key or '（解绑）'}")


def _on_area_fade(self) -> None:
    """框选/多选 → **统一设置淡入淡出时间**（用户："也能一键修改多个 scene 的参数"）。"""
    from PyQt6.QtWidgets import QInputDialog
    from .store_area_ops import _after_area_batch
    ids = _selected_or_band_ids(self)
    if not ids:
        self._status.setText("先在保存区框选或用 Ctrl 多选若干场景")
        return
    val, ok = QInputDialog.getDouble(
        self, "自动淡入淡出", "选中的场景统一淡入淡出时间（秒，0 = 瞬时）：",
        0.5, 0.0, 30.0, 2)
    if not ok:
        return
    try:
        from ...processing.dispatch import get_effect_cache_store
        store = get_effect_cache_store()
        for cid in ids:
            store.set_fade(cid, float(val))
    except Exception:
        # R181 P2-3：批量改淡化时间失败 → 用户以为设了实际没设，留痕
        _log.debug("批量设置淡化时间失败 ids=%d val=%.2f", len(ids), float(val),
                   exc_info=True)
    _after_area_batch(self, f"已把 {len(ids)} 个场景淡入淡出设为 {float(val):.2f}s")


AREA_BATCH_HANDLERS = (_on_area_call, _on_area_off, _on_area_mode,
                       _on_area_bind_key, _on_area_fade,
                       _deactivate_or_release_inactive)

__all__ = ["AREA_BATCH_HANDLERS", "_selected_or_band_ids", "_on_area_call",
           "_on_area_off", "_on_area_mode", "_on_area_bind_key", "_on_area_fade",
           "_deactivate_or_release_inactive"]
