# -*- coding: utf-8 -*-
"""存储区**框选批量操作**（一键开启 / 一键关闭 / 一键改触发模式）。

用户 2026-09-14：
  "需要你能够把保存区也支持框选，框选可以一键开启多个保存的 scene，也能一键改变
   多个 scene 的触发模式。"

从 `store_handlers` 拆出（该文件此前超门禁 500 行线）。动作仍由 `_install_store_handlers`
挂到 `DispatchPanel` 上，语义归属不变；只依赖面板既有服务（`_recall_ids`/
`_deactivate_scene`/`refresh_saved`/`cacheChanged`），零耦合。
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QMenu

_log = logging.getLogger(__name__)

# ★ R183 T8：鼠标按住 flash 的失焦兜底释放（实现拆到 `mouse_flash_release`，
#   本文件只做装配名引用 —— 该拆分是为了守住 500 行单文件上限）。
# 导入位置必须在 `_on_store_pressed` 定义**之前**（模块级函数体内引用这两个名字）。
from .mouse_flash_release import (_release_mouse_flash,  # noqa: E402
                                  mouse_held_add as _mouse_held_add,
                                  mouse_held_discard as _mouse_held_discard,
                                  release_mouse_flash)

#: 框选菜单项 → (键, 动作 mode, 标签)；None mode = 走专项处理函数
#: （★ 清查⑧：E 批外提 `store_area_batch` 时这两个元组被漏带 → 右键菜单
#:   NameError；R183-⑧ 修复：常量回迁本文件——菜单装配与选择分派都在此）
_AREA_ITEMS = (
    ("count_on", None, "一键开启选中的场景"),
    ("count_off", None, "一键关闭选中的场景"),
    ("latch", "latch", "设触发模式：自锁 Latch"),
    ("flash", "flash", "设触发模式：点动 Flash"),
)

#: 框选批量项（键 → 标签；专项动作见 `_dispatch_area_choice`）
_AREA_BATCH_ITEMS = (
    ("bind", "绑定键盘映射…（选中的场景都绑同一键）"),
    ("fade", "自动淡入淡出时间…（选中的场景统一设置）"),
)


# ── 共享小工具（store_handlers 也从这里 import，避免两处各写一份而漂移）──────
def _cache_of(cid: str):
    """按 id 取场景（取不到 None，绝不抛）。"""
    try:
        from ...processing.dispatch import get_effect_cache_store
        return get_effect_cache_store().get(str(cid))
    except Exception:
        # R181 P2-3：场景缓存不可用 → 释放/淡出链静默跳过，留痕
        _log.debug("场景缓存不可用 cid=%s", cid, exc_info=True)
        return None


def _active_set(self) -> set:
    """当前激活集（cue 高亮与开关判定用）。"""
    try:
        return set(self.list.active_ids() or ())
    except Exception:
        return set()


def _set_active(self, ids) -> None:
    """写回激活集（只改高亮，不碰数据）。"""
    try:
        self.list.set_active(ids)
    except Exception:
        pass


def _mode_of(c) -> str:
    """场景触发模式（归一化；非法/缺失回落 latch）——统一走 effective_mode。"""
    return _scene_mode_of(c)


_MODE_LABELS = {"latch": "自锁 Latch（点一下保持、再点关闭）",
                "flash": "点动 Flash（按住才应用、松手释放）"}


def _selected_or_band_ids(self) -> list:
    """当前作用对象：优先框选命中，其次已有选中集。"""
    try:
        band = list(self.list.band_selected_ids() or [])
        if band:
            return band
    except Exception:
        pass
    try:
        return [str(x) for x in (self.list.selected_ids() or [])]
    except Exception:
        return []


def _store_or_none():
    """取效果缓存库（取不到 None）。"""
    try:
        from ...processing.dispatch import get_effect_cache_store
        return get_effect_cache_store()
    except Exception:
        return None


def _after_area_batch(self, msg: str) -> None:
    """批量改动后刷新 + 置脏（随项目持久化）。"""
    try:
        self.refresh_saved()
    except Exception:
        pass
    try:
        self.cacheChanged.emit()
    except Exception:
        pass
    try:
        self._status.setText(msg)
    except Exception:
        pass


def _on_store_area_menu(self, global_pos) -> None:
    """保存区空白/框选处右键：一键开启/关闭/改模式（作用于框选或已选）。"""
    n = len(_selected_or_band_ids(self))
    m = QMenu(self)
    acts = {}
    for key, mode, label in _AREA_ITEMS:
        acts[key] = m.addAction(f"{label}（{n}）" if mode is None else label)
    m.addSeparator()
    for key, label in _AREA_BATCH_ITEMS:
        acts[key] = m.addAction(label)
    _apply_area_icons(m)
    chosen = m.exec(global_pos)
    if chosen is None:
        return
    _dispatch_area_choice(self, chosen, acts)


def _apply_area_icons(menu) -> None:
    """框选菜单也配前端风格图标（复用 clip 右击菜单的既有工具）。"""
    try:
        from ..timeline.clip_context_menu import ensure_menu_action_icons
        ensure_menu_action_icons(menu)
    except Exception:
        pass


def _dispatch_area_choice(self, chosen, acts) -> None:
    """框选菜单选择 → 动作（查表，避免长 if/elif 链）。"""
    if chosen is acts["count_on"]:
        return _on_area_call(self)
    if chosen is acts["count_off"]:
        return _on_area_off(self)
    if chosen is acts.get("bind"):
        return _on_area_bind_key(self)
    if chosen is acts.get("fade"):
        return _on_area_fade(self)
    for key, mode, _label in _AREA_ITEMS:
        if mode and chosen is acts[key]:
            return _on_area_mode(self, mode)


# ── flash 模式的按住/松开（用户 2026-09-14："需要鼠标点到才应用，松手就放开
#    release"）：这两个接收视图的**原始鼠标事件**，只对 flash 模式生效。─────────
def _scene_mode_of(c) -> str:
    """场景触发模式（归一化；非法/缺失回落 latch）。"""
    try:
        return str(c.effective_mode())
    except Exception:
        try:
            return str(getattr(c, "mode", "latch") or "latch").lower()
        except Exception:
            return "latch"


def _on_store_pressed(self, entry_id) -> None:
    """flash：**按下即应用**（按住期间保持激活）。

    接管（确保出光 + LTP 重 stamp）已由 `_recall_ids` 的纯调用路径统一完成，
    此处不重复调用（同一场景连 stamp 两次没有额外语义，只是白付一次地址换算）。
    """
    cid = str(entry_id or "")
    c = _cache_or_none(cid) if cid else None
    if c is None or _scene_mode_of(c) != "flash":
        return
    # ★ R183 T8：登记"鼠标按住的 flash" —— 失焦兜底释放的判据（见 release_mouse_flash）
    _mouse_held_add(self, cid)
    _fade_in_scene(self, c)          # ★ 淡入与模式无关（用户 2026-09-14）
    self._recall_ids([cid])
    _note_scene_activation(self, cid)   # 依次恢复性缓存：登记激活顺序（问题7）
    try:
        active = set(self.list.active_ids() or ())
        active.add(cid)
        self.list.set_active(active)
    except Exception:
        pass


def _on_store_released(self, entry_id) -> None:
    """flash：**松手即释放**（真正的点动语义）。

    ★★ 2026-09-14 用户实报："flash 这个模式下很不灵敏，我的鼠标快速点，监控区反应
      不过来。" 实测（600 台 / 有内容监控区）：一次完整点动 **31ms**、连点 5 次 164ms
      → 每次点击都要等满一轮才响应，快速连点必然堆积。

    真因：原实现走完整 `_deactivate_scene`（**全控制台清台**：`release_all_editor`
    把每个编辑层在所有域上清空 + 清 marks + 再 `refresh_monitor` 一次）。对**点动**
    而言这是过度的 —— 点动只需要"把这次点出来的效果收回去"，不该清掉操作员正在
    编辑的其它东西。

    → 点动释放改为**轻量**：只停本场景各面板的输出（真 release 的最小充分集），
      不做全局清台、不重复刷新（面板停输出的 report_to_bus 会让监控区在下一个
      快/慢 tick 自然更新）。语义更正确，且每次点动省掉一整轮全清。
    """
    cid = str(entry_id or "")
    _mouse_held_discard(self, cid)     # ★ R183 T8：退出"鼠标按住"集合
    if not _is_active_flash(self, cid):
        return
    c = _cache_or_none(cid)
    _set_active(self, _active_set(self) - {cid})
    # ★ 淡出：有淡化时间就先降增益，**降完再**真释放（顺序很重要，见 scene_fade）
    # ★★ 2026-09-18 回访修正（flash + 淡化 → "回到上一个"失效）：原实现在淡出
    #   **进行中**就调 `_restore_previous_scene` —— 那时本场景的层增益还在往 0 走，
    #   而斜坡收尾的 `_release_scene_outputs` 又会调面板 release（programmer/matrix
    #   是**整层清空**）→ 刚回放的上一个场景被一起擦掉。
    #   实测（A latch pump=128 → B flash fade=0.5s 按住/松开）：松手瞬间读出 128，
    #   0.5s 后（斜坡走完）读出 **0**，A 永久消失（用户："第二个取消后应回到第一个"）。
    #   修法：回放与"停面板输出"绑在同一条收尾里 —— 无淡化时立即、有淡化时等斜坡结束。
    def _finish_flash_release() -> None:
        _release_scene_outputs(self, cid)
        # ★ R183 审计⑦B P2-8 修复（flash 松手不摘 stamp 台账）：latch 链的收尾
        #   `_finish_scene_release` 摘账，而 flash 走的是这条轻量收尾 —— 缺摘账时
        #   已释放场景的记账残留，`_blocked_addrs` 会用它继续**幽灵压制**低优先级
        #   场景（那些通道再也抢不到）。与 latch 链同一函数、同一点位。
        _drop_stamp_ledger(cid)
        _restore_previous_scene(self, cid)

    if not _fade_out_scene(self, c, on_done=_finish_flash_release):
        _finish_flash_release()
    # ★ R181 P2-4：flash 松手释放同样归还推杆缩放 → 推杆窗滑杆同步归 100
    #   （`_fade_out_scene` 未设淡化时已 drop；设了淡化的走回调后 drop）——
    #   此处覆盖"有淡化"的分支，保证 UI 与 scale 不失同步。
    try:
        from .scene_fade_ops import _reset_takeover_windows
        _reset_takeover_windows(self, [cid])
    except Exception:
        # R181 P2-3：推杆窗复位失败 → UI 与实际 scale 失同步
        _log.debug("flash 释放后复位推杆窗失败 cid=%s", cid, exc_info=True)
    try:
        self._status.setText(f"已释放「{getattr(c, 'name', '')}」（flash 点动）")
    except Exception:
        # R181 P2-3：仅状态栏文案失败（释放已完成），留痕即可
        _log.debug("flash 释放状态栏更新失败 cid=%s", cid, exc_info=True)


def _drop_stamp_ledger(cid: str) -> None:
    """摘掉某场景的优先级压制记账（从 scene_release 借同一实现，避免两套语义）。

    ★ 为什么转发而不是直接 import `scene_takeover`：摘账在 `scene_release` 里
      已有唯一实现（含异常留痕），本模块只按需调用 —— 新增一处 import 就等于
      给"语义漂移"开第二个入口。
    """
    try:
        from .scene_release import _drop_stamp_ledger as _impl
        _impl(str(cid or ""))
    except Exception:
        _log.debug("压制记账摘账失败 cid=%s", cid, exc_info=True)


def _is_active_flash(self, cid: str) -> bool:
    """该事件 id 是否是"处于激活态的 flash 场景"（松手释放的前置守卫）。"""
    if not cid:
        return False
    c = _cache_or_none(cid)
    if c is None or _scene_mode_of(c) != "flash":
        return False
    try:
        return cid in set(self.list.active_ids() or ())
    except Exception:
        return False


def _release_scene_outputs(self, cid: str) -> None:
    """轻量释放：只把该场景的节点交给各面板**停输出**（不全局清台、不刷新）。

    `release_nodes` 由 MainWindow 注入（各面板 `dispatch_release_state` → 停面板
    定时器），面板停输出后其 `report_to_bus(force=True)` 会让监控区在下一 tick
    自然收敛 —— 故这里不需要额外的 `refresh_monitor`。
    """
    fn = getattr(self, "_release_nodes", None)
    entries = _entries_of(cid)
    if callable(fn) and entries:
        try:
            fn(entries)
        except Exception:
            # R181 P2-3：轻量释放失败 = 被释放场景的面板仍在输出，留痕
            _log.debug("轻量释放停节点失败 cid=%s nodes=%d", cid, len(entries),
                       exc_info=True)


def _entries_of(cid: str) -> list:
    """场景条目 → recall/release 通用 entries 列表（取不到给空列表）。"""
    try:
        from ...processing.dispatch import get_effect_cache_store
        c = get_effect_cache_store().get(str(cid))
    except Exception:
        c = None
    if c is None:
        return []
    return [{"node": dict(e.get("node", {}) or {}),
             "state": dict(e.get("state", {}) or {})}
            for e in (getattr(c, "nodes", []) or [])]


# ★ R194/S2-02：死代码 _teardown_scene_hooks 已删除（全仓零调用方；其职责由
#   wire_zone_teardown 的 destroyed 回调 + _teardown_captured 承担）。


def _teardown_captured(key_filter, fade_runner) -> None:
    """销毁回调：**只**操作连接时捕获好的对象，绝不回查已删除的 `self`。

    原写法 `lambda: self._teardown_scene_hooks()` 在 `destroyed` 触发时 `self` 的 C++
    对象已析构 → `RuntimeError: wrapped C/C++ object ... has been deleted`（Qt 事件循环
    里炸出来，用例直接失败）。

    ★ R194/S2-02：`fade_runner` 实参支持可变 cell（单元素 list，懒建点登记）——
      构造期 `_fade_runner` 尚未懒建，直接 getattr 捕获恒 None，安全网成死路径。
    """
    if isinstance(fade_runner, list):
        fade_runner = fade_runner[0] if fade_runner else None
    try:
        if key_filter is not None:
            key_filter.uninstall()
    except Exception:
        # R181 P2-3：销毁回调摘过滤器失败（Qt 析构期，仅留痕）
        _log.debug("销毁回调摘按键过滤器失败", exc_info=True)
    try:
        if fade_runner is not None:
            fade_runner.reset()
    except Exception:
        # R181 P2-3：销毁回调归还层增益失败 → 图层可能残留 0.0 被永久静音
        _log.debug("销毁回调归还层增益失败（该层可能残留静音）", exc_info=True)


# ★ R183 审计⑦B P0-1：框选批量动作拆到 `store_area_batch`（本文件守 500 行上限）；
#   本文件只做装配名引用，装配顺序 = `AREA_HANDLERS` 元组顺序。
from .store_area_batch import (_deactivate_or_release_inactive,  # noqa: E402
                               _on_area_bind_key, _on_area_call,
                               _on_area_fade, _on_area_mode, _on_area_off,
                               _selected_or_band_ids)

AREA_HANDLERS = (_on_store_area_menu, _on_area_call, _on_area_off, _on_area_mode,
                 _on_area_bind_key, _on_area_fade,
                 _on_store_pressed, _on_store_released,
                 release_mouse_flash, _release_mouse_flash,
                 _deactivate_or_release_inactive)

#: 淡入淡出接线在 `scene_fade_ops`（本文件转发，保持既有 import 位置不变）
from .scene_fade_ops import (_cache_or_none, _drop_scene_fade,  # noqa: E402
                             _fade_in_scene, _fade_out_scene,
                             _reset_scene_fades, _scene_layers)

def _note_scene_activation(panel, cid: str) -> None:
    """转发到 `scene_release._note_scene_activation`（**延迟导入**破循环依赖）。

    ★ scene_release 在模块级 import 本文件的 `_cache_of`/`_fade_out_scene`，
      故本文件不能在模块级反向 import 它 —— 用函数内导入。
    """
    try:
        from .scene_release import _note_scene_activation as _impl
        _impl(panel, cid)
    except Exception:
        pass


def _restore_previous_scene(panel, cid: str) -> bool:
    """转发到 `scene_release._restore_previous_scene`（延迟导入，同上）。"""
    try:
        from .scene_release import _restore_previous_scene as _impl
        return bool(_impl(panel, cid))
    except Exception:
        return False


# ★ R194/S2-02：死代码 _teardown_scene_hooks 已删除（全仓零调用方；其职责由
#   wire_zone_teardown 的 destroyed 回调 + _teardown_captured 承担）。


__all__ = ["AREA_HANDLERS", "_selected_or_band_ids", "_on_store_area_menu",
           "_scene_layers", "_fade_in_scene", "_fade_out_scene", "_drop_scene_fade",
           "_reset_scene_fades", "_release_scene_outputs",
           "_on_area_call", "_on_area_off", "_on_area_mode",
           "_on_store_pressed", "_on_store_released", "_scene_mode_of",
           "_cache_of", "_cache_or_none", "_active_set", "_set_active", "_mode_of",
           "_note_scene_activation", "_restore_previous_scene",
           "release_mouse_flash", "_deactivate_or_release_inactive"]