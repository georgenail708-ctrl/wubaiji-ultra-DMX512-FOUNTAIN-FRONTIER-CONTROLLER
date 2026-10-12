# -*- coding: utf-8 -*-
"""存储区动作（场景的存 / 取 / 更 / 删 / 触发 / 释放）。

★ 触发模式三态（用户 2026-09-14 修正："你实际上 hold 跟 flash 没分开"）：
    latch 自锁（点一下保持、再点关闭）/ flash 点动（按住才应用、松手释放）/
    hold 保持（点一下应用并保持，释放走右键「关闭」）。
  flash 的按住/松开由**视图原始鼠标事件**驱动，见 `store_area_ops`。

★ 纯点击场景卡**不**改设备选中、**不**调入监控区（用户补充："仅仅作为一个效果的
  启用播放"）；只有「编辑（调入监控区后修改）」才切选中/监控区，且**不 release**
  既有效果（保留但不显示）。

★ 释放必须**停各面板输出**（只清 NetworkManager 层会被面板定时器下一帧灌回，
  用户实报"点了关闭也消不掉"）。走 MainWindow 注入的 `release_nodes`。

本文件由 `_install_store_handlers(DispatchPanel)` 以 setattr 挂到面板类上
（不往 god-class 类体堆方法，见 CLAUDE.md 10.1）。
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from typing import Optional

_log = logging.getLogger(__name__)

from . import scene_keys as _keys
from .scene_release import (_clear_scene_activations, _deactivate_scene,
                            _deactivate_from_ui, _note_scene_activation,
                            _release_before_delete)
from .scene_menu import (_PROMPT_SPECS, _apply_menu_icons, _build_scene_menu,
                         _scene_delete, _scene_edit, _scene_prompt, _set_menu_checks)
from .store_binding_ops import (BINDING_HANDLERS, _bind_key, _set_fade,
                                _open_fader_takeover, _set_priority)
from .store_area_ops import (AREA_HANDLERS, _active_set, _cache_of,
                             _fade_in_scene, _fade_out_scene, _mode_of,
                             _reset_scene_fades, _scene_mode_of, _set_active)
# ★ R183 T6/T7：跑灯 + 场景页（模块级装配，见各自模块）
from .chase_runner_ui import (_chase_configure, _chase_toggle,
                              _install_chase_handlers, teardown_chase)
from .scene_pages import _copy_to_page, _move_to_page


#: 共享小工具（`_cache_of`/`_active_set`/`_set_active`/`_mode_of`）来自 store_area_ops，
#: 避免两处各写一份而漂移；释放链（停面板/清台/熄高亮）来自 scene_release。


def _on_store_activated(self, entry_id):
    """左键点场景卡 → 按**触发模式**响应（用户 2026-09-14 定案/修正）。

    ★★ 三种模式（用户实报："你实际上 hold 跟 flash 没分开…现在应该纠正成 flash，
      因为它需要鼠标点到才应用，松手就放开 release"）：

      · **latch**（自锁/开关）：点一下**应用并保持**（高亮），再点一下**关闭**；
      · **flash**（点动/瞬时）：**按住才应用、松手即释放** —— 由 `slot_pressed`/
        `slot_released` 驱动（见 store_area_ops），本函数**不**对它做点击切换；
      · **hold**（保持）：点一下**应用并保持**，**不因再点而关闭**（释放走右键菜单）。
    """
    cid = str(entry_id or "")
    if not cid:
        return
    c = _cache_of(cid)
    if c is None:
        return
    # ★ R183 T6：跑灯序列卡走状态机（点一次起/再点停），不参与 latch 点开关
    if dict(getattr(c, "chase", {}) or {}).get("steps"):
        self._chase_toggle(cid)
        return
    mode = _mode_of(c)
    if mode == "flash":
        return          # 由按住/松开事件驱动（避免点击逻辑与释放打架）
    if cid in _active_set(self):
        self._deactivate_scene(cid)          # 自锁：再点即关
        return
    # ★ 淡入（与 flash 共用同一份逻辑，**与模式无关** —— 用户 2026-09-14）
    _fade_in_scene(self, c)
    self._recall_ids([cid])
    # ★ 依次恢复性缓存（问题7）：登记激活顺序，释放后据此"回到上一个"
    _note_scene_activation(self, cid)
    _set_active(self, _active_set(self) | {cid})


def _on_store_empty_slot(self, position):
    """点存储区**空格** → 询问名称 → 存"当前正在编辑的**全部**效果"。

    ★ 用户 2026-09-14 定案："我要保存一个效果，直接让用户点击保存区任意一个格子，
      然后就弹出询问效果名称，然后就直接把效果存进格子了" —— 故这里**不**看监控区
      的格选中，一律存当前全部效果（名称由 `_do_save` 内的 QInputDialog 询问）。
    ★ 排除 clip：监控区已与 clip 无关，故存场景也不把 timeline clip 算进去。
    """
    nodes = _current_effect_nodes(self)
    if not nodes:
        QMessageBox.information(
            self, "调度台",
            "当前没有正在编辑的效果。\n先在各页面做效果，再点这里保存。")
        return
    # ★ R178 存前确认（用户痛点"存效果乱存"）：把将存的节点清单亮给用户
    #   （面板/效果名/设备组），确认才落盘 —— `_current_effect_nodes` 已排除
    #   clip 与"仅被调用在播放"（extra["recalled"]）的节点，这里的清单即终局。
    from .store_confirm import confirm_save_nodes
    if not confirm_save_nodes(self, nodes):
        return
    # `_do_save` 内部会在存档成功后就地自动清台（见 panel_library 的说明）。
    self._do_save(nodes, position=int(position))


def _release_after_save(self) -> None:
    """存场景后自动清台：**停各面板输出** + 清层 + 清高亮 + 刷新监控区。

    ★★ 2026-09-14 用户实报："effect 和 matrix 等页面制作的效果被存储区存了之后
      也没有正确 release 并同步到相应效果页面。"
    真因：原实现只清 NetworkManager 层 + 清 registry，**没停各面板输出** →
    面板定时器下一帧把效果写回（清不掉），面板自身的 playing/transport 状态也
    没复位（"没同步到相应效果页面"）。故必须先停面板、再清台。

    顺序（缺一不可）：
      ① 停各面板输出（走 `release_nodes` → 各面板 `dispatch_release_state`）；
      ② NetworkManager 编辑层整域清空（把残留真值归零）；
      ③ 调用层注册表清空（叠加语义复位，下次调用行为正确）；
      ④ 高亮 + 视图值缓存作废 + 刷新（否则监控区还显示旧值）。
    """
    _release_all_panels(self)
    # ★★ R136：顺序**不可颠倒** —— `_reset_scene_fades` 会触发淡出遗留的释放回调
    #   （`_finish_scene_release` → `_restore_previous_scene`），激活顺序还在就会把
    #   "仍记账"的场景重新回放（清台后它满值复活；见 probe_1b_clear_recall.py）；
    #   顺序本身 = "谁还亮着"的第二份记账，清台须一并归零（2026-09-18 回访修正）。
    _clear_scene_activations(self)
    _reset_scene_fades(self)          # ★ 清台即无场景点亮 → 归还全部层增益（防残留记账）
    try:
        ops = getattr(self, "_cell_ops", None)
        if ops is not None and hasattr(ops, "release_all_editor"):
            ops.release_all_editor()
    except Exception:
        # R181 P2-3：清台时释放编辑层失败 → 静默残留
        _log.debug("存后清台释放编辑层失败", exc_info=True)
    _detach_all_editor_sources(self)
    try:
        from ...processing.dispatch import get_release_registry
        get_release_registry().clear()
    except Exception:
        # R181 P2-3：调用层注册表没清 → 叠加语义错乱（下次调用行为不对）
        _log.debug("存后清台复位调用层注册表失败", exc_info=True)
    try:
        self.list.clear_active()
    except Exception:
        _log.debug("存后清台熄灭激活高亮失败", exc_info=True)
    try:
        self.view.clear_value_caches()      # 值缓存作废（防旧值残留）
    except Exception:
        _log.debug("存后清台作废值缓存失败", exc_info=True)
    try:
        self.refresh_monitor()
        self._status.setText("已存场景并自动清台（各面板输出已停、通道已释放）")
    except Exception:
        _log.debug("存后清台刷新监控区失败", exc_info=True)


def _detach_all_editor_sources(panel) -> None:
    """Detach panel-owned authoring state after a successful scene save.

    ``release_nodes`` only visits nodes currently visible on the bus.  A stale
    panel cache can therefore survive outside that list and be captured by the
    next save.  Use each panel's existing release entry point, then clear the
    aggregate bookkeeping; UI parameter widgets and layout caches stay intact.
    """
    provider = getattr(panel, "_panel_provider", None)
    if callable(provider):
        for pid in ("programmer", "effect", "matrix", "linker", "audio", "route", "clip"):
            _detach_editor_source(provider, pid)
    try:
        from ...processing.dispatch import EffectBus, get_release_registry
        EffectBus.instance().clear()
        get_release_registry().clear()
    except Exception:
        _log.debug("存后清理总线/调用层登记失败", exc_info=True)


def _detach_editor_source(provider, pid: str) -> None:
    try:
        widget = provider(pid)
    except Exception:
        widget = None
    if widget is None:
        return
    try:
        handler = _editor_source_handler(widget, pid)
        if handler is not None:
            handler()
    except Exception:
        _log.debug("存后脱钩面板失败 pid=%s", pid, exc_info=True)


def _editor_source_handler(widget, pid: str):
    if pid == "matrix":
        from ..matrix_slideshow.matrix_running_outputs import stop_all
        return lambda: stop_all(widget)
    method_name = {"programmer": "clear_live_output", "effect": "stop_external",
                   "audio": "stop_effect_output"}.get(pid)
    if method_name:
        handler = getattr(widget, method_name, None)
        if callable(handler):
            return handler
    handler = getattr(widget, "dispatch_release_state", None)
    if callable(handler):
        return lambda: handler({})
    return None


def _release_all_panels(self) -> int:
    """把"当前总线上**正在产出的全部**节点"交给各面板停止输出。返回节点数。

    ★★ R131 用户实报（存场景自动清台后效果**自己满值复活**）：原先取
      `_current_effect_nodes`（按用户 2026-09-14 定案**排除 `recalled`**）→ 被"纯调用"
      点亮的面板从未收到 release，keepalive 仍在跑 → 后续只清 NM 层，下一拍被写回
      （实测：存场景瞬间 {}，800ms 后 {0:200,2:210}，高亮已灭无从再关）。

    语义纠正：**"存什么"与"停什么"是两件事** —— 存场景用 `_current_effect_nodes`
      （排除 recalled）；清台用**总线全部节点**（含 recalled）。
    """
    fn = getattr(self, "_release_nodes", None)
    if not callable(fn):
        return 0
    try:                              # ★ 取总线**全部**节点（含 recalled，见上）
        from ...processing.dispatch import EffectBus
        nodes = list(EffectBus.instance().nodes() or [])
    except Exception:
        # R181 P2-3：总线不可用 → 清台停不掉面板（效果会满值复活），留痕
        _log.debug("清台读取 EffectBus 节点失败（效果可能满值复活）", exc_info=True)
        return 0
    entries = [{"node": n.to_dict(), "state": {}} for n in nodes]
    if not entries:
        return 0
    try:
        fn(entries)
        return len(entries)
    except Exception:
        # R181 P2-3：停面板失败 = 清台没做到（下一帧被面板灌回），留痕
        _log.debug("清台停各面板输出失败 nodes=%d（下一帧可能被写回）", len(entries),
                   exc_info=True)
        return 0


def _current_effect_nodes(self) -> list:
    """当前总线上"**我正在编辑的**效果"节点（排除 clip + 排除"只是被调用在播放"的）。

    ★★ 2026-09-14 用户定案：
      "不能让我存新效果的时候存到我已经保存到一个 scene 里面只是正在激活播放的效果。
       即使此时有效果在播放，只要不是我正在编辑的就不存。"

    真因：监控区节点来自 `EffectBus`，而**场景调用**（recall）也是把节点重新推到同一
    条总线上（面板 `report_to_bus`）→ "点了个场景在播"的节点会混进"当前正在编辑的
    效果"，被抄进新场景。

    判据（单一真源在**节点自身**）：面板在场景调用写状态时给该格打
    `extra["recalled"]=True`；用户一旦**真的改**该格（选效果/改参数/播放），面板把该
    标记清掉。保存时排除仍带标记的节点 —— 于是"调入后我又改了它"照样会存（那确实是
    编辑），"我只是让它播"不会存。各面板自管标记，无需跨面板路由。

    ★★ 2026-09-20 用户实报修复（"存储区存效果有时会存储失败，给一个值但什么都没存
      下来；此时一直可以存很多 scene 不停的存，显示一直都可存，但每个点击之后都没有
      效果"）：
      真因 = 本函数读的是 `self.view.nodes()`（**监控区表格里的缓存副本**），而那份
      缓存只在 `refresh_monitor()` 时由定时器 / showEvent 填入。于是当总线已有节点、
      但监控区尚未刷新（面板刚打开还没走完一次 400ms 定时、或计时窗口内刚做完编辑）时，
      这里读到 **0 个节点** → `_do_save` 静默存下**空场景**（视图仍显示"可存"，
      因为空槽本来就一直可点）→ 调用时无值可下发 = 用户看到的"点击之后都没有效果"。
      修：**优先读总线实时节点**（编辑的权威来源），监控区缓存仅作回退。
    """
    nodes = []
    try:
        from ...processing.dispatch import EffectBus
        nodes = list(EffectBus.instance().nodes() or [])
    except Exception:
        nodes = []
    if not nodes:
        try:
            nodes = list(self.view.nodes() or [])
        except Exception:
            return []
    return [n for n in nodes if _is_editable_node(n)]


def _is_editable_node(n) -> bool:
    """该节点是否算"我正在编辑的"（排除 clip 与"只是被调用在播放"的）。"""
    if str(getattr(n, "panel", "") or "") == "clip":
        return False
    try:
        return not bool((getattr(n, "extra", None) or {}).get("recalled"))
    except Exception:
        return True


def _mark_cell_edited(self, node) -> None:
    """用户**真的编辑**了某格 → 清掉"仅调用在播放"标记，使其重新可被保存。

    保留此函数供调用方（面板/测试）显式声明"这是编辑"，与节点标记成对。
    """
    try:
        if node is None:
            return
        extra = getattr(node, "extra", None)
        if isinstance(extra, dict):
            extra.pop("recalled", None)
    except Exception:
        pass


def _on_store_reordered(self, entry_id, old_pos, new_pos):
    """拖拽换位：把卡片移到目标槽位。

    ★★ 2026-09-14 深查修正（场景不可达）：原实现直接 `cur.position = new_pos`，
    **不处理目标槽里的占用者** → 两个场景拿到同一个 position（实测 [1,1]）；
    而视图建槽位映射是 `by_pos[pos] = item`（后写覆盖），于是被覆盖的那张卡
    **在网格里画不出来、也点不到**（用户级"丢失"，且存库后永久）。
    正解：用库里既有的 `EffectCacheStore.move()`（→ `scene_grid.move_position`，
    纯算术交换），保证位置唯一。该方法此前是**死代码**（无人调用）。
    """
    try:
        from ...processing.dispatch import get_effect_cache_store
        _move_scene(get_effect_cache_store(), str(entry_id), int(old_pos), int(new_pos))
        self.refresh_saved()
        self.cacheChanged.emit()
    except Exception:
        pass


def _move_scene(store, entry_id: str, old_pos: int, new_pos: int) -> bool:
    """把 `entry_id` 移到 `new_pos`，**保证位置唯一**（详见 `_on_store_reordered`）。"""

    try:
        if store.move(entry_id, new_pos):
            return True
    except Exception:
        pass
    cur = store.get(entry_id)
    if cur is None:
        return False
    for other in store.ordered():
        if other is not cur and int(getattr(other, "position", 0) or 0) == new_pos:
            other.position = int(old_pos)
    cur.position = int(new_pos)
    return True


def _on_store_menu(self, kind, key, global_pos):
    """Storage context menu: scenes."""
    if kind == "empty":
        self._on_store_empty_slot(int(key))
        return
    if kind != "slot" or not isinstance(key, dict):
        return
    eid = str(key.get("id", ""))
    if not eid:
        return
    self._scene_menu(eid, global_pos)


def _scene_call(self, store, c, cid) -> None:
    """右键「调用（应用）」：应用并在 latch 语义下保持激活（高亮）。

    ★★ 2026-09-18 回访修正（右键调用不参与"依次恢复性缓存"、也不淡入）：本入口
      原先只做 `_recall_ids` + 置高亮 —— 而**其它三个**激活入口（左键点卡
      `_on_store_activated`、flash `_on_store_pressed`、框选 `_on_area_call`）都
      还会 ① `_fade_in_scene`（用户设的自动淡入淡出）② `_note_scene_activation`
      （登记激活顺序，释放时据此"回到上一个"）。
      实测（真实 DispatchPanel + 真场景条目）：左键点 A → 右键调用 B（顺序里只有 A）
      → 关闭 A：programmer 释放是**整层清空**，把同层的 B 一起擦掉，而回放链
      `_restore_previous_scene` 因顺序里没有 B 而**什么都不回放** → B 的卡片还亮着
      却完全不出光（用户级"右键调用的场景被别的场景关掉就再也回不来"），
      且 B 的淡化时间被静默忽略。
      修法：与 `_on_store_pressed` 完全同款四步（淡入 → 调用 → 登记顺序 → 高亮），
      只去掉"按模式分流"，因为菜单项语义就是显式应用。
    """
    try:
        _fade_in_scene(self, c)          # ★ 淡入与模式无关（与其它入口一致）
    except Exception:
        pass
    self._recall_ids([cid])
    _note_scene_activation(self, cid)    # ★ 依次恢复性缓存：登记激活顺序（问题7）
    try:
        active = set(self.list.active_ids() or ())
        active.add(cid)
        self.list.set_active(active)
    except Exception:
        pass


def _scene_off(self, store, c, cid) -> None:
    """右键「关闭（释放该场景）」→ `deactivate_from_ui`（含"未激活不误灭现场"守卫）。"""
    _deactivate_from_ui(self, cid)


def _scene_mode(self, store, c, cid, mode: str) -> None:
    store.set_mode(cid, mode)
    self.refresh_saved()
    self.cacheChanged.emit()
    self._status.setText(
        f"「{c.name}」触发模式 = {'开关 Latch' if mode == 'latch' else '点动 Hold'}")


def _scene_menu(self, cid, global_pos):
    """场景右键菜单：调用 / 关闭 / 编辑 / 触发模式 / 变式 / 重命名 / 备注 / 删除。

    ★ 菜单项 → 动作实现放 `_SCENE_ACTIONS`（查表分发）。原实现是一条 88 行、
      CC 22、嵌套 9 的 if/elif 长链（架构门禁违例）；改表后本函数只做
      "建菜单 → 取选择 → 查表执行"。
    """
    from PyQt6.QtWidgets import QMenu
    from ...processing.dispatch import get_effect_cache_store
    store = get_effect_cache_store()
    c = store.get(cid)
    if c is None:
        return
    # ★ R183 T6：跑灯在跑时菜单首项文案变"停止跑灯"
    try:
        _running = self._chase_is_running(cid)
    except Exception:
        _running = False
    m, acts = _build_scene_menu(self, _mode_of(c), chase_active=_running)
    chosen = m.exec(global_pos)
    if chosen is None:
        return
    fn = _SCENE_ACTIONS.get(acts.get(chosen, ""))
    if fn is not None:
        fn(self, store, c, cid)


def _scene_prompt_bound(self, store, c, cid, kind: str) -> None:
    """面板方法壳：转发到 `scene_menu._scene_prompt`（保持 `_SCENE_ACTIONS` 调用形态）。"""
    _scene_prompt(self, store, c, cid, kind)


#: 菜单 键 → 处理函数（`(s, st, c, cid)` 四参签名；s=面板实例）。
#: ★ 清查⑧A P0-2：变异入口一律 **`getattr(s, name)` 走实例属性**——`wrap_mutating`
#:   把包装器 setattr 到**类**上，而本表若直引模块全局名会绕开包装（菜单路径
#:   永不留撤销条目，实测 5 项）。实例属性解析天然命中类属性=已包装版本。
#: ★ 清查⑧A P0-1：四个新动作（chase/chase_stop/move_page/copy_page）目标函数首参
#:   是位置形参 `panel`，修前以 `self=` 关键字调用 → 点击必 TypeError（功能整体
#:   不可达）。统一位置传参（与 `_scene_prompt_bound` 等同形）。
_SCENE_ACTIONS = {
    "call": _scene_call,
    "off": _scene_off,
    "edit": _scene_edit,
    "variant": lambda s, st, c, cid: _scene_prompt_bound(s, st, c, cid, "variant"),
    "rename": lambda s, st, c, cid: _scene_prompt_bound(s, st, c, cid, "rename"),
    "note": lambda s, st, c, cid: _scene_prompt_bound(s, st, c, cid, "note"),
    "delete": _scene_delete,
    "mode_latch": lambda s, st, c, cid: _scene_mode(s, st, c, cid, "latch"),
    "mode_flash": lambda s, st, c, cid: _scene_mode(s, st, c, cid, "flash"),
    "bind_key": lambda s, st, c, cid: getattr(s, "_bind_key")(c=c, cid=cid),
    "fade": lambda s, st, c, cid: getattr(s, "_set_fade")(c=c, cid=cid),
    "priority": lambda s, st, c, cid: getattr(s, "_set_priority")(c=c, cid=cid),
    "fader_takeover": lambda s, st, c, cid: getattr(s, "_open_fader_takeover")(c=c, cid=cid),
    # ★ R183 T6：跑灯序列（步列表 / RunOrder / 步长；Chaser 语义）
    "chase": lambda s, st, c, cid: getattr(s, "_chase_configure")(cid),
    "chase_stop": lambda s, st, c, cid: getattr(s, "_chase_toggle")(cid),
    # ★ R183 T7：场景页（移动到页 / 复制到页）
    "move_page": lambda s, st, c, cid: getattr(s, "_move_to_page")(cid),
    "copy_page": lambda s, st, c, cid: getattr(s, "_copy_to_page")(cid),
}


STORE_HANDLERS = (
    _on_store_activated,
    _scene_edit,
    _scene_mode,
    _scene_menu,
    _build_scene_menu,
    _set_menu_checks,
    _on_store_empty_slot,
    _release_after_save,
    _deactivate_scene,
    _on_store_reordered,
    _on_store_menu,
    _scene_prompt_bound,
    _scene_edit,
    _scene_delete,
) + BINDING_HANDLERS + AREA_HANDLERS   # 绑定/淡化 + 框选批量（见各自模块）


def _install_store_handlers(cls) -> None:
    """把上面的模块级函数挂到面板类上（幂等）。"""
    for fn in STORE_HANDLERS:
        setattr(cls, fn.__name__, fn)
    _install_chase_handlers(cls)      # ★ R183 T6：跑灯四动作
    from .scene_pages import _install_page_handlers
    _install_page_handlers(cls)       # ★ R183 T7：场景页动作
    # ★ R183 审计⑦B P0-3：给全部"变异库"的入口套"改前压 DOMAIN_DISPATCH 快照"
    #   （必须在所有来源的 setattr **之后**，否则会漏掉后装的入口）
    from .dispatch_undo import wrap_mutating
    wrap_mutating(cls)
