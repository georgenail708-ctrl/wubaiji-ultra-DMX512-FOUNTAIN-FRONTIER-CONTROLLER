# -*- coding: utf-8 -*-
"""跑动场景的工程持久化 + 撤销域登记（R183 F1 第 7 项）。

背景（真工程只读探针 `To The Moon项目.dmxt`）：
  · `route_order_state` 在档（2 页），而 **`route_animator_state` 键不存在**
    ——跑动面板的火车/轨道/道岔场景只活在 `runner_panel._page_scenes`（内存），
    `runner_panel._save_scene` 从不回写工程字段，于是工程另存/重开后场景全丢；
  · `models.Project.route_animator_state` 通道与 `DOMAIN_ROUTE` 撤销域早已存在
    （`project_manager.py:153`），只是写入口没接上。

本模块提供三件事（全部容错：任一失败只记日志，绝不打断面板操作）：
  1. :func:`persist_scene` —— 刷新当前画布一次再回写；
     :func:`persist_saved_scene` 是画布快照尾部的纯导出写穿入口
     ``project.route_animator_state``（旧档缺键 = 空 dict，零迁移）；
  2. :func:`snapshot_route_undo` —— 变更**之前**压一条 `DOMAIN_ROUTE` 域快照，
     让镜像/清空等场景操作可撤销（域口径与 `_reload_panels_from_project`
     的 `route_order_state` 回灌一致）;
  3. :func:`rehydrate_from_project` —— undo/redo 用域合并**重建 Project 对象**
     （工程身份指纹 created_at/name 不变，`runner_panel.set_context` 的换工程
     判据不会触发），此时用工程里的 `route_animator_state` 回灌内存场景，
     否则下一次保存会拿"未回滚的内存"覆盖刚恢复的存档（＝撤销静默无效）。
"""
from __future__ import annotations

import logging
from .runner_lifecycle import hide_view

log = logging.getLogger(__name__)

def _project_of(panel):
    """面板当前工程对象（走面板既有 `_project()`；缺失/异常给 None）。"""
    try:
        proj = panel._project()
    except Exception:
        proj = None
    if proj is not None:
        return proj
    pm = getattr(panel, "_project_manager", None)
    if pm is None:
        return None
    try:
        return (getattr(pm, "project", None)
                or getattr(pm, "_project", None))
    except Exception:
        return None


def export_scene_state(panel) -> dict:
    """原三字段投影；不再进入会调用 persist_scene 的画布快照。"""
    return {
        "runner_pages": {pid: scene for pid, scene in panel._page_scenes.items()},
        "runner_slots": {pid: dict(slots) for pid, slots in panel._save_slots.items()},
        "runner_current_page": panel._current_page_id or "",
    }


def _page_combo_target(panel, pages, previous):
    current = getattr(panel, "_current_page_id", None)
    if current and any(str(pg["id"]) == str(current) for pg in pages):
        return current
    old_page = next((pg for pg in pages if str(pg["id"]) == str(previous)), None)
    if old_page and (old_page["device_count"] > 0
                     or not any(pg["device_count"] > 0 for pg in pages)):
        return previous
    return None


def rebuild_page_combo(panel, pages) -> None:
    """恢复页优先；失效页沿原 combo/设备默认规则，不改共享 TopView。"""
    pc = panel._page_combo
    if pc is None:
        return
    blocked = pc.blockSignals(True)
    try:
        target = _page_combo_target(panel, pages, pc.currentData())
        pc.clear()
        for pg in pages:
            pc.addItem(pg["name"], pg["id"])
        if target and pc.findData(target) >= 0:
            pc.setCurrentIndex(pc.findData(target))
        if pc.currentIndex() < 0 and pc.count() > 0:
            first = next((pg for pg in pages if pg["device_count"] > 0), None)
            idx = pc.findData(first["id"]) if first is not None else 0
            pc.setCurrentIndex(idx if idx >= 0 else 0)
    finally:
        pc.blockSignals(blocked)


def persist_saved_scene(panel) -> bool:
    """把面板场景写回 ``project.route_animator_state``（写穿；失败返回 False）。

    调用点 = `runner_panel._save_scene` 尾部（**单一咽喉**）：轨道绘制/镜像/
    清空/切页/存档槽全部经它落盘，因此工程字段永远与内存场景一致；
    MainWindow 的常规保存链（`_collect_project_state_for_save`）随后原样带出。
    不主动 emit project_changed：`_save_scene` 在切页/轮询路径高频调用，
    置脏由既有 `_on_save_page`/`closed` 信号承担。
    """
    proj = _project_of(panel)
    if proj is None:
        return False
    try:
        state = export_scene_state(panel)
    except Exception:
        log.debug("route_animator: export scene failed", exc_info=True)
        return False
    try:
        proj.route_animator_state = state
    except Exception:
        log.debug("route_animator: persist scene to project failed", exc_info=True)
        return False
    return True


def persist_scene(panel) -> bool:
    """公开写穿入口仍先刷新当前画布一次；尾部写穿走纯导出入口。"""
    proj = _project_of(panel)
    if proj is None:
        return False
    try:
        proj.route_animator_state = panel.save_state()
    except Exception:
        log.debug("route_animator: save scene failed", exc_info=True)
        return False
    return True


def snapshot_route_undo(panel) -> bool:
    """场景变更前压一条 `DOMAIN_ROUTE` 撤销快照（无 PM/失败静默）。

    `DOMAIN_ROUTE = {route_order_state, route_animator_state}`
    （`project_manager.py:153`）——撤销只回滚跑动域，不牵连其他面板改动。
    """
    pm = getattr(panel, "_project_manager", None)
    if pm is None or not hasattr(pm, "_snapshot"):
        return False
    try:
        from ...core.project_manager import DOMAIN_ROUTE
        pm._snapshot(domain=DOMAIN_ROUTE)
    except Exception:
        log.debug("route_animator: undo snapshot failed", exc_info=True)
        return False
    return True


def rehydrate_from_project(panel, proj) -> bool:
    """同一工程的 Project 对象被 undo/redo 重建后，用工程场景回灌面板。

    仅当工程里确有场景（`route_animator_state` 非空）时回灌：空状态不覆盖
    用户内存里还没保存的场景（新建工程/首次打开时本就为空）。
    回灌按"工程为准"整体替换面板内存场景字典——`_save_scene` 已写穿工程
    （见 :func:`persist_scene`），故工程态恒 ≥ 内存态，替换不丢新编辑；
    而 undo 回滚后工程态更旧/更小，替换正是要让它生效。
    """
    state = getattr(proj, "route_animator_state", None)
    if not isinstance(state, dict) or not state:
        return False
    panel._page_scenes = {}
    panel._save_slots = {}
    try:
        panel.load_state(state)
    except Exception:
        log.debug("route_animator: rehydrate from project failed", exc_info=True)
        return False
    return True


def _references_removed_track(node, removed_set) -> bool:
    """该道岔是否引用被删轨道（branch/branches 任一命中）。"""
    bp = node.get("branch_params", {}) or {}
    refs: list = []
    if bp.get("branch"):
        refs.append(str(bp["branch"]))
    refs.extend(str(b) for b in (bp.get("branches") or []))
    return any(r in removed_set for r in refs)


def prune_switches_for_removed_tracks(panel, removed) -> int:
    """删除轨道时顺带移除挂在该轨道/引用该轨道的道岔节点, 避免重建后悬空。

    （R183 F1：自 `runner_panel._prune_switches_for_removed_tracks` **原样搬出**
    ——面板文件已超 4084 行基线，逻辑等价、只换住址；返回剪掉的节点数。）
    """
    switches = getattr(panel, "_switches", None)
    removed_set = {str(r) for r in (removed or ())}
    if not isinstance(switches, dict) or not removed_set:
        return 0
    pruned = 0
    for nid, node in list(switches.items()):
        hit = (str(node.get("track_id", "") or "") in removed_set
               or _references_removed_track(node, removed_set))
        if hit:
            switches.pop(nid, None)
            pruned += 1
    return pruned


def drop_tracks(panel, removed) -> int:
    """删除一批轨道 + 剪掉挂其上的道岔（R9：变更前压 DOMAIN_ROUTE 快照）。

    清轨/删轨**唯一出口**（面板源码只留一行调用，逻辑全在本模块）。
    返回实际删除条数。
    """
    removed_set = {str(r) for r in (removed or ()) if r}
    if not removed_set:
        return 0
    snapshot_route_undo(panel)
    tracks = getattr(panel, "_tracks", None)
    if isinstance(tracks, dict):
        for rid in removed_set:
            tracks.pop(rid, None)
    prune_switches_for_removed_tracks(panel, removed_set)
    return len(removed_set)


def drop_switch(panel, node_id) -> bool:
    """摘除一个道岔节点（R9：摘除前压 DOMAIN_ROUTE 快照）。返回是否真删。"""
    switches = getattr(panel, "_switches", None)
    if not isinstance(switches, dict) or str(node_id) not in switches:
        return False
    snapshot_route_undo(panel)
    switches.pop(str(node_id), None)
    return True


def sync_after_project_swap(panel) -> bool:
    """`set_context` 尾部调用：换工程/undo 重建对象后让内存场景与工程一致。

    · 换工程（身份变化）→ `set_context` 已清空内存字典，此处把**新工程**里
      存着场景灌回（旧档缺 `route_animator_state` 键 → 空 dict → 零迁移）；
    · undo/redo（身份不变、Project 对象重建）→ 工程里的场景是回滚后的真源，
      必须回灌，否则下一次保存会用"没回滚的内存"覆盖它（撤销静默无效）。
    """
    proj = _project_of(panel)
    if proj is None:
        return False
    return rehydrate_from_project(panel, proj)


def close_panel(panel) -> None:
    """面板关闭的容错序列（R183 W2：原 `RouteRunnerPanel.closeEvent` 逐行搬出）。

    每步独立 try：隐藏 UI/存场景/回写工程字段/发 closed 信号，
    任一步失败只记日志——页面必须能关掉，不留"死页"。`super().closeEvent(event)`
    仍留在面板侧（事件对象不出面板）。
    """
    for step in _CLOSE_STEPS:
        try:
            step(panel)
        except Exception:
            log.debug("route_animator: close step %s failed", step.__name__,
                      exc_info=True)


def _close_save_scene(panel) -> None:
    panel._save_scene(panel._current_page_id)


def _close_persist_state(panel) -> None:
    proj = panel._project()
    if proj is not None:
        proj.route_animator_state = panel.save_state()


def _close_emit_closed(panel) -> None:
    panel.closed.emit()


#: 关闭视图保留原场景保存与 closed 通知，不释放活跃输出。
_CLOSE_STEPS = (hide_view, _close_save_scene, _close_persist_state,
                _close_emit_closed)


__all__ = ["export_scene_state", "rebuild_page_combo", "persist_scene",
           "persist_saved_scene",
           "snapshot_route_undo", "rehydrate_from_project",
           "drop_tracks", "drop_switch", "prune_switches_for_removed_tracks",
           "sync_after_project_swap", "close_panel"]
