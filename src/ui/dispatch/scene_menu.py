# -*- coding: utf-8 -*-
"""场景右键菜单的**构建**与名称类动作（从 `store_handlers` 拆出，守 500 行单文件上限）。

拆法按职责：`store_handlers` 保留"动作语义"（存/取/关/删/编辑 + 释放链），
本模块只负责"把菜单画出来"与"改名字"这类无状态辅助。

★ 三个名称类动作（复制为变式 / 重命名 / 备注）与菜单勾选态的唯一真源在这里；
  R70 曾因重构误删 `_PROMPT_SPECS` 导致三项直接 `NameError`，故本表与消费函数同文件。
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from .scene_release import (_note_scene_activation, _release_before_delete)

log = logging.getLogger(__name__)


#: 名称类动作 → `(对话框标题, 默认值取值函数)`
#: ★ R70 修复：R60 重构时本表被误删，`_scene_prompt` 直接 `NameError`
#:   → 右击「重命名 / 复制为变式 / 备注」三项全部崩（`store_handlers` 内无定义）。
_PROMPT_SPECS = {
    "variant": ("复制为变式", lambda c: f"{c.name} 变式"),
    "rename": ("重命名", lambda c: c.name),
    "note": ("备注", lambda c: c.note),
}


def _scene_prompt(self, store, c, cid, kind: str) -> None:
    """名称类动作（变式/重命名/备注）：弹框 → 写库 → 刷新。"""
    title, default_of = _PROMPT_SPECS[kind]
    text, ok = QInputDialog.getText(self, title, f"{title}：", text=default_of(c))
    if not ok:
        return
    text = str(text)
    if kind == "note":
        store.set_note(cid, text)
    elif kind == "variant":
        store.duplicate(cid, text.strip()) if text.strip() else None
    elif text.strip():
        store.rename(cid, text.strip())
    self.refresh_saved()
    self.cacheChanged.emit()


def _scene_delete(self, store, c, cid) -> None:
    if QMessageBox.question(self, "调度台",
                            f"删除场景「{c.name}」？") != QMessageBox.StandardButton.Yes:
        return
    # ★ 2026-09-14 深查修正 M3：删除前先把该场景的输出**收回**（否则删掉一个正在
    #   激活的 scene 后，它的节点仍在面板里输出、监控区一直停在它的值上）。
    # ★★ 2026-09-18 回访修正（带淡化的场景"删不干净"）：见 `_release_before_delete`。
    # ★ R181 P2-4：释放完成后推杆窗滑杆归 100（scale 已归还），再关窗。
    _release_before_delete(self, cid, c)
    try:
        from .fader_takeover import close_takeover_window, reset_takeover_window
        reset_takeover_window(self, cid)
        close_takeover_window(self, cid)
    except Exception:
        # R181 P2-3：推杆窗收口失败 → 死场景 id 残留窗
        log.debug("删除场景后推杆窗收口失败 cid=%s", cid, exc_info=True)
    store.remove(cid)
    self.refresh_saved()
    self.cacheChanged.emit()


def _scene_edit(self, store, c, cid) -> None:
    """**调入监控区修改**：把监控区切到"只显示该场景的设备"，改完覆盖存回。

    ★★ 用户 2026-09-14 定案：
      "如果启用'调入监控区修改'…把监控区切换为仅显示我新调入的设备。（并且 topview
       和各种页面的设备选中状态也切过去）如果之前存在正在编辑的效果和设备组，
       不 release，保留，但不显示…如果我框到之前的设备才继续显示状态。"

    → `sync_selection=True` 回写 TopView 选中（监控区按其 provider 自然只显示新设备）；
      **不 release 任何东西**（旧效果继续输出，只是"当前没选中"故不显示）。
    """
    self._editing_id = cid
    self._recall_ids([cid], sync_selection=True)
    # ★★ R131 修复（编辑中的场景不被当"亮着" → 关任何别的场景就把它整层擦掉）：
    #   其余四个激活入口都会 `_note_scene_activation` + 置高亮，只有"编辑"漏了 →
    #   关别的场景时 `_clear_editor_layers` 的 `others` 为空 → 走全域 `release_all_editor()`
    #   把它写在同一层的值一起清空，而 `_restore_previous_scene` 也回放不出来
    #   （用户"光没了、也不高亮、还没入口点回去"）。编辑也算"亮着"。
    #   **刻意不加** `_activate_takeover` —— 用户明确要求编辑语义不 stamp 接管。
    _note_scene_activation(self, cid)
    try:
        self.list.set_active(set(self.list.active_ids() or ()) | {cid})
    except Exception:
        pass
    try:
        want = set()
        for e in c.nodes:
            nd = dict(e.get("node", {}))
            want.add((str(nd.get("panel", "")), str(nd.get("track_type", "")),
                      str(nd.get("group_key", ""))))
        self.view.set_selected(want)
        self._on_monitor_selection(want)
    except Exception:
        pass
    self.refresh_monitor(force=True)      # 选中集已变 → 强制重建为"只显示新设备"
    self._status.setText(
        f"编辑中：「{c.name}」已调入监控区（此前效果保留未释放）；改完点「覆盖更新」存回"
    )


_MODE_LABELS = {"latch": "自锁 Latch（点一下保持、再点关闭）",
                "flash": "点动 Flash（按住才应用、松手释放）"}


def _build_scene_menu(parent, mode: str, chase_active: bool = False):
    """构建菜单；返回 `(menu, {action: 动作键})`（键 → `_SCENE_ACTIONS`）。

    ★ R183 T6/T7：新增"跑灯序列…（Chaser 四值 RunOrder）"与
    "移动到页 / 复制到页"（场景页）；`chase_active` 决定播放项文案（启动/停止）。
    """
    from PyQt6.QtWidgets import QMenu
    m = QMenu(parent)
    keys = {}
    _seen_side = set()
    for label, key, side in (
            ("调用（应用）", "call", "top"),
            ("关闭（释放该场景）", "off", "top"),
            ("编辑（调入监控区后修改）", "edit", "top"),
            ("触发模式：自锁 Latch（点一下保持，再点关闭）", "mode_latch", "mode"),
            ("触发模式：点动 Flash（按住才应用，松手释放）", "mode_flash", "mode"),
            # ★ 用户 2026-09-14：绑定键盘 + 自动淡入淡出（都放右击菜单）
            ("绑定键盘映射…", "bind_key", "keybind"),
            ("自动淡入淡出时间…", "fade", "keybind"),
            # ★ R178 革新：优先级（1-5 档，高优先级场景压制低优先级的通道接管）
            ("优先级（1-5，高者保通道）…", "priority", "keybind"),
            # ★ R178 革新：场景推杆（per-scene 增益上限，实时拖动）
            ("推杆接管（场景增益）…", "fader_takeover", "keybind"),
            # ★ R183 T6：跑灯序列（步列表 + Loop/SingleShot/PingPong/Random）
            ("跑灯序列…（步进 / 顺序 / 步长）", "chase", "chase"),
            *((("停止跑灯", "chase_stop", "chase"),) if chase_active else ()),
            # ★ R183 T7：场景页
            ("移动到页…", "move_page", "page"),
            ("复制到页…", "copy_page", "page"),
            ("复制为变式…", "variant", "edit2"),
            ("重命名…", "rename", "edit2"),
            ("备注…", "note", "edit2"),
            ("删除", "delete", "edit2")):
        if side != "top" and side not in _seen_side and keys:
            m.addSeparator()      # 非首项且换了分组 → 分隔（跑灯/页/编辑各组各一段）
        _seen_side.add(side)
        keys[m.addAction(label)] = key
    _set_menu_checks(m, keys, mode)
    _apply_menu_icons(m)
    return m, keys


def _apply_menu_icons(menu) -> None:
    """给菜单（含子菜单）补齐前端风格矢量图标。

    ★ 复用 clip 右击菜单的既有工具 `ensure_menu_action_icons`（单一真源，
      配图风格与 clip 菜单一致 —— 用户 2026-09-14："参考 clip 的右击菜单的设计"）。
    """
    try:
        from ..timeline.clip_context_menu import ensure_menu_action_icons
        ensure_menu_action_icons(menu)
    except Exception:
        pass


def _set_menu_checks(m, keys, mode: str) -> None:
    """模式三项显示为勾选态（当前模式打勾）。"""
    want = {"mode_latch": "latch", "mode_flash": "flash"}
    for act, key in keys.items():
        if key in want:
            act.setCheckable(True)
            act.setChecked(mode == want[key])
