# -*- coding: utf-8 -*-
"""场景**释放链**（真正让面板停输出、清台、熄灭高亮）。

用户 2026-09-14 实报：
  "再点一次（hold 模式）它却没有 release，一直消不掉。而且 latch 模式…依然该
   release 的时候不 release。"
  "effect 和 matrix 等页面制作的效果被存储区存了之后也没有正确 release 并同步到
   相应效果页面。"

真因：只登记 release registry + 清 NetworkManager 层是不够的；**各面板自己的输出
定时器仍在每帧把效果写回**（effect `_play_timer`、matrix 播放定时器、programmer
keepalive）→ 清掉的层下一帧被灌回 → "点了关闭也消不掉、面板没同步"。
正确语义：关闭 = 让**面板停止产出**（`dispatch_release_state`）；清层只是结果。

从 `store_handlers` 拆出（该文件此前触门禁 500 行线）。仍由 `_install_store_handlers`
挂到 `DispatchPanel` 上，语义归属不变。
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

from .store_area_ops import _cache_of, _entries_of, _fade_out_scene


def _deactivate_scene(self, cid: str) -> None:
    """关闭某场景：停各面板输出 + 释放调用层 + 清台 + 熄灭高亮。

    ★ 淡出优先：有淡化时间就先降增益。**降完再**停面板，否则面板一停层就空了，
      增益斜坡没东西可淡（直接跳变，淡出形同虚设）。

    调用约定：本函数 = "**释放该场景**"，不判断它是否激活（程序化路径/测试依赖此语义）；
      "关闭一张**没在播放**的卡会误灭现场"的守卫放在 UI 入口 `_scene_off`（见那里）。
    """
    cid = str(cid or "")
    _c = _cache_of(cid)
    if _c is not None and _fade_out_scene(
            self, _c, on_done=lambda: _finish_scene_release(self, cid)):
        # ★ 深查修正 L3：淡出期间**只**熄灭高亮（立即视觉反馈）；真正的收尾
        #   （停面板 / 清层 / 刷新监控区）等增益降到 0 之后由 `_finish_scene_release` 做。
        _unhighlight_scene(self, cid)
        return
    _finish_scene_release(self, cid)


def _deactivate_from_ui(panel, cid: str) -> None:
    """UI 入口「关闭（释放该场景）」：**未激活的场景直接跳过**。

    ★★ R131 用户实报（关闭一张**没在播放**的卡会灭掉用户正在编辑的现场输出）：
      原来 UI 无条件走 `_deactivate_scene` → 收尾里 ① `_release_scene_nodes` 调各面板
      `dispatch_release_state`（Programmer 实现是 `clear_live_output()`：停定时器 +
      **整层清空** fader/programmer/tester/audio/joystick，不只该场景的设备）；
      ② `_clear_editor_layers` 见 `others` 空就全域清台 → 用户手调着的现场光当场全灭
      （实测：编辑中 fader {4:88} → 关闭一张从没点过的卡 → {}）。

    守卫**刻意放在 UI 层**：`_deactivate_scene` 的语义是"释放该场景"，程序化路径与
    既有测试依赖它无条件释放；"用户点了一张没亮的卡"才该被拦。
    """
    cid = str(cid or "")
    try:
        active = set(panel.list.active_ids() or ())
    except Exception:
        active = set()
    if cid not in active:
        try:
            from .scene_fade_ops import _drop_scene_fade
            _drop_scene_fade(panel, cid)     # 未激活本不该有斜坡，防御性丢弃
        except Exception:
            pass
        try:
            panel._status.setText("该场景当前未在播放（无需关闭）")
        except Exception:
            pass
        return
    _deactivate_scene(panel, cid)


def _finish_scene_release(panel, cid: str) -> None:
    """场景释放的**真正收尾**（瞬时释放与淡出结束共用同一条路径）。

    ★ 深查修正 L3：淡出结束时也必须走完这四步 —— 原实现淡出回调只调
      `_release_scene_nodes`，于是"淡出关闭"之后编辑层没清、监控区没刷新，
      用户看到值还停在原地。收尾动作只有一处，两条路径不可能再漂移。
    """
    cid = str(cid or "")
    _release_scene_nodes(panel, cid)         # ① 停各面板输出（真 release）
    _mark_scene_released(cid)                # ② 调用层登记（clip 轨迹跳过表）
    _drop_stamp_ledger(cid)                  # ②' 优先级压制记账摘账（R178）
    _clear_editor_layers(panel, cid)         # ③ 清台兜底（仅当无其它场景还亮着）
    _unhighlight_scene(panel, cid)           # ④ 熄灭高亮 + 清选中态 + 值缓存
    # ⑤ 依次恢复性缓存：还有别的场景亮着 → 回放上一个（问题7，见该函数说明）
    _restore_previous_scene(panel, cid)
    try:
        panel.refresh_monitor()
        panel._status.setText("已关闭该场景（已停各面板输出并清台）")
    except Exception:
        # R181 P2-3：收尾已完成，仅刷新/文案失败 → 留痕（行为不变）
        _log.debug("场景释放收尾刷新失败 cid=%s", cid, exc_info=True)


def _note_scene_activation(panel, cid: str) -> None:
    """登记场景激活顺序（最近激活的排在最后）——"依次恢复性缓存"的真源。

    ★★ 2026-09-18 用户定案（问题7）：多场景写到同一轨道时，释放后要能**回到上一个**。
      用户原话："如果多开到同一个轨道，遵循跟 clip 轨道一样的**后发接管**原则，
      并且注意**依次恢复性缓存**。…第二个效果取消之后，能够回到第一个。"
    """
    try:
        order = list(getattr(panel, "_scene_activation_order", None) or [])
        cid = str(cid or "")
        if not cid:
            return
        order = [c for c in order if c != cid]
        order.append(cid)
        panel._scene_activation_order = order
    except Exception:
        pass


def _forget_scene_activation(panel, cid: str) -> list:
    """把该场景移出激活顺序；返回**仍在激活的**顺序列表（最后=最近激活）。"""
    try:
        order = [c for c in (getattr(panel, "_scene_activation_order", None) or [])
                 if str(c) != str(cid or "")]
        panel._scene_activation_order = order
        return order
    except Exception:
        return []


def _clear_scene_activations(panel) -> None:
    """**全局清台**时归还激活顺序（"依次恢复性缓存"必须与激活集同步清空）。

    ★★ 2026-09-18 回访修正（清台后仍能"复活"旧场景）：激活顺序是"谁还亮着"的
      **第二份记账**，而全局清台（存场景后自动清台 `_release_after_save`）只清了
      激活高亮（`list.clear_active()`）与各层，**没清这个列表** →
      之后释放任何一个新场景，`_restore_previous_scene` 会把早已被清台的旧场景
      **重新放出来**（幽灵出光）。
      实测：点 A(programmer pump=128) → 保存新场景（自动清台、高亮已空）→ 点 B(200)
      → 取消 B：输出变成 **A 的 128**（期望全 0；`_scene_activation_order` 里仍留着 A）。
      "清台 = 没有任何场景亮着"，故这里把记账一并归零。
    """
    try:
        panel._scene_activation_order = []
    except Exception:
        # R181 P2-3：清台没归零激活顺序 → 释放新场景会幽灵复活旧场景
        _log.debug("清台归还激活顺序失败（旧场景可能幽灵复活）", exc_info=True)
    try:
        from .scene_takeover import clear_stamp_ledger
        clear_stamp_ledger()     # ★ R178：清台 = 无场景亮着 → 压制记账一并归零
    except Exception:
        # R181 P2-3：压制记账不清 → 清台后仍可能残留压制
        _log.debug("清台归还压制记账失败", exc_info=True)


def _scene_is_active(panel, cid: str):
    """场景是否在激活高亮集（取不到 → None=无从判定，走保守激活路径）。"""
    try:
        return str(cid or "") in {str(x) for x in (panel.list.active_ids() or ())}
    except Exception:
        return None


def _release_before_delete(panel, cid: str, cache) -> bool:
    """删除场景**之前**把它的输出收回（M3 + 2026-09-18 带淡化回访，见
    `_release_active_scene`；未激活卡走 `_release_inactive_only`——清查②#3）。

    ★ 2026-09-14（M3）：删除前不收回 → 它的节点仍在面板里输出，而"关闭"入口
      已随卡片消失 → 监控区永久停在被删场景的值上。
    """
    # _scene_is_active 三态：True=激活 / False=未激活（仅收输出）/
    # None=无从判定（保守走激活路径——测试桩与异构面板安全方向）。
    if _scene_is_active(panel, cid) is False:
        _release_inactive_only(panel, cid)
        return False
    return _release_active_scene(panel, cid, cache)


def _release_active_scene(panel, cid: str, cache) -> bool:
    """删除**激活**场景：带淡化走"弃斜坡+就地完整收尾"，否则瞬时释放；
    第三层兜底（`_release_inactive_only`）含 stamp 摘账（波5 低危清零）。

    ★★ 2026-09-18 回访（带淡化"删不干净"）：`_deactivate_scene` 对有淡化场景
      只降增益+登记回调就返回，调用方随即 remove → 收尾链回库取条目全部落空
      （面板不停输出、清台不做、监控区不刷新）。修法：先同步丢弃斜坡并**就地**
      走完整收尾，复用收尾单一实现。
    """
    try:
        has_fade = float(cache.fade_seconds()) > 0.0
    except Exception:
        has_fade = False
    if has_fade:
        try:
            from .scene_fade_ops import _drop_scene_fade
            _drop_scene_fade(panel, cid)         # 先撤销斜坡，防收尾后回调二次触发
            _finish_scene_release(panel, cid)    # 立刻走完整收尾（不等斜坡）
            return True
        except Exception:
            # R181 P2-3：删除链吞错 → 改为可诊断（debug 含栈；失败仍走下方兜底）
            _log.debug("带淡化场景删除前收尾失败，回退瞬时释放路径 cid=%s", cid,
                       exc_info=True)
    try:
        _deactivate_scene(panel, cid)
        return True
    except Exception:
        # 第三层兜底（R183 波5：含 stamp 摘账——上面两条路径各自会摘，唯独本
        # 路径曾漏；台账残留=幽灵压制低优先级场景）。停输出失败也摘账。
        _log.debug("删除前瞬时释放失败，回退直接停节点 cid=%s", cid, exc_info=True)
        _release_inactive_only(panel, cid)
        return False


def reset_scene_runtime_state(panel) -> None:
    """**场景库整体换新**（新建工程 / 打开工程 / 首次懒建）时归还全部运行期记账。

    ★★ 2026-09-18 回访修正（工程切换后的幽灵出光 + 残留增益）：两条"库换新"路径
      （`_reset_dispatch_singletons` 新建、`_load_project` 打开）只做了
      `store.from_dict(...)` + `refresh_saved()`，**既没清激活顺序、也没清淡化记账**。

      第二个是**第二份真源**（"谁还亮着"除激活高亮外还有这一份），于是实测：
      点 A（高亮 + 顺序 [A]）→ 新建工程（库已空、高亮已空，**顺序仍 [A]**）→
      点新工程的 B → 关闭 B：`_restore_previous_scene` 把**上一个工程的 A**
      重新放出来（幽灵出光）。淡化记账同理：残留的 `_layer_gains` 条目会让
      新工程的层被持续压暗（旧工程残留的 0.0 = 永久静音）。

      统一收口（与 `_release_after_save` 的清台同一套动作），凡"库换新"都必须调。
    """
    _clear_scene_activations(panel)
    try:
        panel.list.clear_active()
    except Exception:
        # R181 P2-3：库换新后高亮没熄 → 旧工程残留高亮（幽灵"亮着"）
        _log.debug("库换新熄灭激活高亮失败（可能残留幽灵高亮）", exc_info=True)
    try:
        panel.view.clear_value_caches()
    except Exception:
        # R181 P2-3：值缓存不清 → 监控区显示上一工程残留值
        _log.debug("库换新作废值缓存失败（监控区可能显示旧值）", exc_info=True)
    try:
        from .scene_fade_ops import _reset_scene_fades
        _reset_scene_fades(panel)
    except Exception:
        # R181 P2-3：淡化记账不归还 → 新工程图层被旧增益持续压暗/静音
        _log.debug("库换新归还淡化记账失败（新工程图层可能被压暗）", exc_info=True)


def _restore_previous_scene(panel, cid: str) -> bool:
    """释放某场景后，把**上一个仍激活的场景**重新调起来（依次恢复性缓存）。

    ★★ 2026-09-18 用户实报（问题7）**根因**：释放链只停**本场景**的面板输出，
      但 programmer / matrix 的释放是**整层清空**（`clear_layer("fader")` /
      `clear_layer("matrix")`）→ 把前一个场景写在同一层的值**一起擦掉** →
      "取消第二个回不到第一个"。用户观察到的"先点 effect 存的可以、先点 programmer
      存的就不行"正是这个差异：effect 释放是**按格弹出**（只撤自己那格），
      programmer/matrix 是整层清空。

      修法（对齐 clip 轨道的后发接管语义）：释放后若还有别的场景亮着，把**最后激活
      的那个**重新调一遍 —— recall 是幂等的，重放会把它的值重新写回层。
      这样"后发接管 → 释放 → 回到前一个"闭环成立，且不依赖各面板的释放粒度。

    返回是否做了回放。
    """
    order = _forget_scene_activation(panel, cid)
    if not order:
        return False
    # ★★ 2026-09-18 回访修正（整层清空把**所有**前序场景一起擦掉）：按**激活顺序**
    #   （旧→新）回放**全部**幸存场景（而非只回放 order[-1]）—— programmer/matrix
    #   的释放是整层清空，同层更早的场景不在任何面板的"当前输出"里，单次回放救不
    #   回来。recall 幂等，后回放者胜出 → 与"后发接管"的层内优先级一致；属于被
    #   释放场景、且不在任何幸存场景里的通道保持清空（真正的 release）。
    # ★★ R178 交叉淡化：被释放场景**配了淡化**（即本函数多半在淡出斜坡走完后
    #   才被 on_done 调到这里）且幸存场景自己也配了 fade_in>0 时，把重 recall
    #   包在 `_fade_in_scene`（0→1 淡入）里 —— 与刚结束的淡出形成时序配对，
    #   "旧场景压下去、上一个场景浮上来"。保守条件：任一方没配淡化则维持
    #   原行为（瞬时重放），绝不引入新的亮度跳变。
    crossfade = _released_had_fade(cid)
    ok = False
    for prev in order:
        try:
            if crossfade:
                _fade_in_survivor(panel, prev)
            panel._recall_ids([prev])
            ok = True
        except Exception:
            _log.debug("回放上一个场景失败（不影响已完成的释放）", exc_info=True)
    return ok


def _released_had_fade(cid: str) -> bool:
    """被释放的场景是否配了淡化秒数（交叉淡化的前提之一）。"""
    c = _cache_of(cid)
    try:
        return c is not None and float(c.fade_seconds()) > 0.0
    except Exception:
        return False


def _fade_in_survivor(panel, prev_cid: str) -> None:
    """给一个幸存场景登记淡入重放（未配 fade_in>0 则什么都不做，原行为）。"""
    c = _cache_of(prev_cid)
    if c is None:
        return
    try:
        if float(c.fade_seconds()) <= 0.0:
            return
    except Exception:
        return
    try:
        from .scene_fade_ops import _fade_in_scene
        _fade_in_scene(panel, c)
    except Exception:
        pass


def _release_scene_nodes(self, cid: str) -> int:
    """把该场景的每个节点交给对应面板**停止输出**。返回处理的节点数。

    走 MainWindow 注入的 `release_nodes` 路由（与 `recall_nodes` 对称）——
    调度台不 import 任何效果面板，路由仍是主窗职责。
    """
    fn = getattr(self, "_release_nodes", None)
    if not callable(fn):
        return 0
    entries = _entries_of(cid)
    if not entries:
        return 0
    try:
        fn(entries)
        return len(entries)
    except Exception:
        # R181 P2-3：停输出失败 = 释放没做成（该场景仍在输出），必须留痕
        _log.debug("场景释放停节点失败 cid=%s nodes=%d", cid, len(entries),
                   exc_info=True)
        return 0


def _mark_scene_released(cid: str) -> None:
    """登记该场景的调用层已释放（给 clip 轨迹回放的跳过表看）。"""
    try:
        from ...processing.dispatch import get_release_registry
        get_release_registry().release_clip(str(cid))
    except Exception:
        # R181 P2-3：跳过表登记失败 → clip 轨迹可能仍回放已释放场景
        _log.debug("释放登记写入失败 cid=%s", cid, exc_info=True)


def _release_inactive_only(panel, cid: str) -> None:
    """仅收输出（fade 撤销+真停面板输出）+ stamp 摘账——不清编辑层/不回放。

    两个消费者：① 清查②#3 删除未激活场景卡（删除未在跑的 cue 对
    现场零副作用——无场景亮着时删卡曾把现场手调的编辑层整层清空）；
    ② 波5 第三层兜底（停输出失败也摘账，防幽灵压制）。
    """
    try:
        from .store_area_ops import (_drop_scene_fade as _drop,
                                     _release_scene_outputs)
        _drop(panel, cid)
        _release_scene_outputs(panel, cid)
    except Exception:
        _log.debug("仅收输出失败 cid=%s", cid, exc_info=True)
    _drop_stamp_ledger(cid)


def _drop_stamp_ledger(cid: str) -> None:
    """释放场景 → 摘掉它的优先级压制记账（R178，见 `scene_takeover`）。"""
    try:
        from .scene_takeover import note_scene_released
        note_scene_released(str(cid or ""))
    except Exception:
        # R181 P2-3：压制记账摘不掉 → 旧场景可能幽灵压制新场景
        _log.debug("压制记账摘账失败 cid=%s", cid, exc_info=True)


def _clear_editor_layers(panel, cid: str = "") -> None:
    """清空编辑层（清台兜底）—— **仅当没有别的场景还亮着**。

    ★★ 2026-09-14 深查修正（关 A 灭 B）：`release_all_editor` 会清**所有**域的所有
    编辑层。若用户同时开着场景 A 和 B，关闭 A 会把 B 的 programmer 静态值 / linker
    输出一起抹掉（效果类会自愈，静态类不会）→ 明显的错误行为。
    语义收敛为：关闭最后一个激活场景时才做全清台（那时本来就没有别人的输出）；
    还有别的场景亮着时，只靠上一步的 `_release_scene_nodes`（按节点停面板）就够了。
    """
    try:
        others = set(panel.list.active_ids() or ()) - {str(cid or "")}
    except Exception:
        others = set()
    if others:
        return
    try:
        ops = getattr(panel, "_cell_ops", None)
        if ops is not None and hasattr(ops, "release_all_editor"):
            ops.release_all_editor()
    except Exception:
        # R181 P2-3：清台兜底失败 = 释放最后一个场景后编辑层可能仍留值
        _log.debug("释放最后一个场景后清编辑层失败 cid=%s", cid, exc_info=True)


def _unhighlight_scene(panel, cid: str) -> None:
    """熄灭该卡的激活高亮，并清掉其**选中态**（否则卡片仍以"已选中"亮着，
    看起来像还在激活 —— 用户报"latch 高亮反馈不正确"）。"""
    try:
        active = set(panel.list.active_ids() or ())
        active.discard(cid)
        panel.list.set_active(active)
        sel = set(panel.list.selected_ids() or ())
        if cid in sel:
            sel.discard(cid)
            panel.list.set_selected(sel)
    except Exception:
        pass
    try:
        panel.view.clear_value_caches()      # 值缓存作废（防旧值残留）
    except Exception:
        pass


__all__ = ["_deactivate_scene", "_deactivate_from_ui", "_finish_scene_release", "_release_scene_nodes",
           "_mark_scene_released", "_clear_editor_layers", "_unhighlight_scene",
           "_note_scene_activation", "_forget_scene_activation",
           "_clear_scene_activations", "_restore_previous_scene",
           "_release_before_delete", "reset_scene_runtime_state"]
