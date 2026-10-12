# -*- coding: utf-8 -*-
"""调度台还原 / 释放的**主窗侧流程**（从 `main_window` 拆出，god-class 禁新增方法）。

`MainWindow` 是五个上帝类之一（CLAUDE.md 10.1）：禁止新增方法。故还原/释放的路由
逻辑以模块级函数 + `window` 参数实现，主窗只保留一行转发壳。

两条路线的**行为边界**（用户 2026-09-14 定案）：
  · 纯点击场景卡 → 只让效果播放，**不动**设备选中、**不动**监控区；
  · 「编辑（调入监控区修改）」→ 回写 TopView 选中（监控区随之只显示新调入的设备），
    且**不 release** 既有效果（保留但不显示，框回旧设备即恢复显示）。
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


class _RecallAccumulator:
    """还原时收集涉及设备（保序去重）。"""

    def __init__(self) -> None:
        self.ids: list = []
        self._seen: set = set()

    def add(self, fids) -> None:
        for fid in (fids or ()):
            sf = str(fid)
            if sf and sf not in self._seen:
                self._seen.add(sf)
                self.ids.append(sf)


#: 能理解 `for_edit` 语义位的面板回调（其余面板走两参旧契约，自动降级）。
_EDIT_AWARE_RECALL = "__accepts_for_edit__"


def recall_nodes(window, entries: list, sync_selection: bool = False) -> bool:
    """把缓存节点还原到各自面板。返回是否全部成功。

    ★★ 2026-09-18 用户定案（**调用 vs 编辑 两个语义**，问题9 根因）：
      用户原话："我点击一个效果，它达到的目的应该是**只播放效果**而不调入相应页面的
      控件，也就是**调用的语义**。而你现在制作的是，一调用就自动带上了**编辑的语义**，
      也就是把控件都还原了。"

      真因：`sync_selection` 这一位**只用来决定要不要回写 TopView 选中**，从未下传给
      面板的 `dispatch_recall_state` → 面板无法区分两种入口，一律做全量 UI 还原
      （`load_state` 整页控件重建 + 刷控件 + 切设备组）。

      修法：把语义**一路传到面板**（`for_edit` 关键字）：
        · 左键点场景卡 / 一键开启 → `sync_selection=False` → `for_edit=False`
          = **纯播放**：只恢复该格作用域并把参数交给引擎跑，**不动任何控件**；
        · 右键「编辑（调入监控区后修改）」→ `sync_selection=True` → `for_edit=True`
          = 播放 + 还原控件 + 切 TopView 选中（监控区随之只显示该组设备）。

      ★ 附带收益：纯播放**跳过 `load_state`**（整页控件树重建）——那正是"点了之后
        卡一秒"的最大一块（见 effect `_load_preset_params` 重建全部参数控件）。
        故本改动同时解决问题 2 的卡顿。
    """
    acc = _RecallAccumulator()
    results = [_recall_one(window, e, acc, for_edit=sync_selection)
               for e in (entries or [])]
    if acc.ids and sync_selection:
        _sync_recall_selection(window, acc.ids)
    return all(results) if results else True


def _recall_one(window, entry, acc, *, for_edit: bool = False) -> bool:
    """还原单个节点（顺带把涉及设备并入累加器）；返回是否成功。

    `for_edit=False`（默认）= 纯播放语义（见 `recall_nodes`）。
    """
    try:
        node = dict((entry or {}).get("node", {}) or {})
        state = dict((entry or {}).get("state", {}) or {})
        acc.add(node.get("fixture_ids", []))
        rec = _recall_callable_for(window, str(node.get("panel", "") or ""))
        return _invoke_recall(rec, node, state, for_edit)
    except Exception:
        _log.warning("调度台还原节点失败", exc_info=True)
        return False


def _invoke_recall(rec, node, state, for_edit: bool) -> bool:
    """调用面板 recall 回调；按能力声明决定是否传 `for_edit`。"""
    if not callable(rec):
        return False
    if not getattr(rec, _EDIT_AWARE_RECALL, False):
        return bool(rec(node, state))       # 未升级面板：两参旧契约
    try:
        return bool(rec(node, state, for_edit=bool(for_edit)))
    except TypeError:                        # 手工绑定的回调不接关键字
        return bool(rec(node, state))


def _recall_callable_for(window, panel: str):
    """取该面板的还原回调（面板未建时先懒建再取）。取不到返回 None。"""
    rec = (window._dispatch_panel_targets().get(panel) or {}).get("recall")
    if callable(rec):
        return rec
    window._ensure_dispatch_panel(panel)
    return (window._dispatch_panel_targets().get(panel) or {}).get("recall")


def _ensure_topview_loaded(window):
    """懒建 TopView 面板（未建过时返回 None）。

    ★★ 2026-09-18 用户实报（问题8："调入监控区编辑无法实现"）：场景**自带**保存时
      的设备组信息，调入编辑应等价于"在 TopView 里选中该设备组"→ 监控区随之只显示
      这批设备。但本模块的 `_sync_recall_selection` 见 `_topview_widget is None`
      就**静默返回** —— 而 TopView 是**懒加载**的（只在 `_lazy_load_panels` 里赋值，
      见 main_window），用户没打开过 TopView 时它恒为 None → **设备选中丢失 →
      监控区出不来对应设备组**（用户原话"由于没有接线，导致调入监控区编辑无法实现"）。
      修法：这里按需懒建 TopView（与 `_ensure_dispatch_panel` 同思路），建不出来
      才退回原行为。
    """
    tv = getattr(window, "_topview_widget", None)
    if tv is not None:
        return tv
    dock = getattr(window, "_topview_dock", None)
    if dock is not None:
        try:
            window._lazy_load_panels(dock)
        except Exception:
            _log.warning("调度台还原：TopView 懒建失败", exc_info=True)
    return getattr(window, "_topview_widget", None)


def _sync_recall_selection(window, union: list) -> None:
    """把合并后的设备集回写 TopView 选中（仅"调入监控区修改"时调用）。"""
    try:
        tv = _ensure_topview_loaded(window)
        if tv is None:
            return
        pm = getattr(window, "_pm", None)
        proj = getattr(pm, "project", None) if pm is not None else None
        present = {str(getattr(f, "id", "")) for f in
                   (getattr(proj, "fixtures", []) or [])}
        ordered = [f for f in union if not present or f in present]
        if not ordered:
            return
        # ★★ 必须用 `set_external_fixture_selection`（**跨页**可见集）：
        #   `_set_selection` 按**当前页**过滤，跨 sheet 的设备会被全滤掉 → 选中 0 台
        #   （代码注释已记这个坑：#22b 根因）。实测：4889 台工程里选 60 台跨页设备，
        #   用 `_set_selection` 结果为 0。
        # ★★ 2026-09-18 用户实报（问题8 第二层根因）：TopView 的**默认画板是空的**
        #   —— 设备要由用户从展板拖入（或从工程/导入恢复页数据）才会进
        #   `page["visible_fixture_ids"]`，而 `set_external_fixture_selection` 内部
        #   会用 `_all_pages_visible_ids()` 过滤 → **页上没有的设备被全部滤掉 →
        #   选中为空 → 监控区出不来设备组**（用户原话"调入监控区编辑无法实现"）。
        #   语义上"调入监控区编辑某场景" ≡ "把该场景的设备组放进画板并选中"
        #   （用户："场景自带当时存的设备组信息，调入编辑应等同于在 TopView 里
        #   选中那个设备组"），故这里先把缺失设备放进当前画板再选中。
        _ensure_ids_on_canvas(tv, ordered)
        if hasattr(tv, "set_external_fixture_selection"):
            tv.set_external_fixture_selection(ordered, emit=False)
        else:
            tv._set_selection(ordered, emit=False)
        window._sync_effect_browser_topview_selection(ordered)
    except Exception:
        _log.warning("调度台还原：TopView 选中回写失败", exc_info=True)


def _ensure_ids_on_canvas(tv, ordered: list) -> None:
    """把不在任何画板页上的设备放进当前页（否则选中会被可见集过滤掉）。

    ★ 2026-09-18 问题8 二层根因：TopView 默认画板为空，设备要拖入才可见；
      `set_external_fixture_selection` 按 `_all_pages_visible_ids()` 过滤 →
      页上没有的设备全被滤掉 → 监控区出不来设备组。
    """
    try:
        vis = set(tv._all_pages_visible_ids())
        missing = [fid for fid in ordered if fid not in vis]
        if not missing:
            return
        from PyQt6.QtCore import QPointF
        rect = tv._canvas.rect() if hasattr(tv, "_canvas") else None
        center = QPointF(rect.center()) if rect is not None else QPointF(0.0, 0.0)
        tv.add_fixture_group_to_canvas(missing, center)
    except Exception:
        _log.warning("调度台还原：把设备组放入画板失败", exc_info=True)


def release_one_node(window, entry) -> bool:
    """释放单个节点到对应面板（面板未实现 release 时视为成功，如 audio/joystick）。"""
    try:
        node = dict((entry or {}).get("node", {}) or {})
        state = dict((entry or {}).get("state", {}) or {})
        rel = _release_callable_for(window, str(node.get("panel", "") or ""))
        if rel is None:
            return True
        return bool(rel(node, state))
    except Exception:
        _log.warning("调度台释放节点失败", exc_info=True)
        return False


def _release_batch_key(window, entry):
    try:
        panel = (entry.get('node') or {}).get('panel', '')
        handler = (window._dispatch_panel_targets().get(panel) or {}).get('release')
        if getattr(handler, '_dispatch_release_whole_panel', False):
            return (id(getattr(handler, '__self__', None)), id(getattr(handler, '__func__', handler)))
    except Exception:
        _log.debug('Cannot inspect release scope; retain individual releases', exc_info=True)
    return None


def release_nodes(window, entries: list) -> bool:
    """把缓存节点从各面板**释放**（关闭场景 / 取消激活时回调）。返回是否全部成功。

    ★★ 用户 2026-09-14 实报："再点一次（hold 模式）它却没有 release，一直消不掉。"
    真因：关闭场景只清 NetworkManager 层，而**面板自己的输出定时器仍在每帧写回** →
    必须走各面板 `dispatch_release_state`（停面板输出），本函数是那条唯一路由。
    """
    ok = True
    released_scopes = set()
    for e in (entries or []):
        key = _release_batch_key(window, e)
        if key is not None and key in released_scopes:
            continue
        if release_one_node(window, e):
            released_scopes.add(key)
        else:
            ok = False
    return ok


def _release_callable_for(window, panel: str):
    """取该面板的释放回调；面板未建时先懒建再取。取不到返回 None。"""
    rel = (window._dispatch_panel_targets().get(panel) or {}).get("release")
    if callable(rel):
        return rel
    window._ensure_dispatch_panel(panel)
    rel = (window._dispatch_panel_targets().get(panel) or {}).get("release")
    return rel if callable(rel) else None


#: 预热计划：(延迟 ms, widget 属性, dock 属性)。
#:   ★ 实测懒建耗时（700 台工程）：topview ≈30ms / programmer ≈122ms /
#:     effect ≈188ms / matrix ≈645ms —— **matrix 最贵**，用户首次点开它最卡。
#:   ★★ 调度台（`_dispatch_widget`）**刻意不在名单里**：实测单独懒建它后进程
#:     **退出期**必崩（rc=139 access violation，`app.exec()` 返回 0 正常，
#:     崩在解释器清理阶段）；其它面板逐个预热均 rc=0。那是调度台自身的既有隐患
#:     （表格模型/定时器/总线连接），与预热无关 —— 但预热会把它暴露在
#:     "用户没开过、关程序时却存在"的状态，故保持它按需懒建。
_WARMUP_PLAN = (
    (0,    "_topview_widget",        "_topview_dock"),
    (600,  "_programmer_widget",     "_programmer_dock"),
    (1500, "_effect_browser_widget", "_effect_browser_dock"),
    (2500, "_matrix_pattern_widget", "_matrix_pattern_dock"),
)


def _warm_alive(window) -> bool:
    """窗口是否还活着（防定时器在窗口销毁后触发）。"""
    try:
        import sip
        return not sip.isdeleted(window)
    except Exception:
        try:
            return window.isVisible() or window.parent() is None
        except Exception:
            return False


def _warm_one(window, attr_widget: str, attr_dock: str) -> None:
    """按需懒建一个面板（已建过或 dock 不存在则跳过；失败静默）。"""
    try:
        if getattr(window, attr_widget, None) is not None:
            return
        dock = getattr(window, attr_dock, None)
        if dock is not None:
            window._lazy_load_panels(dock)
    except Exception:
        _log.debug("面板预热失败（不影响使用）", exc_info=True)


def _warm_all(window) -> None:
    """把 `_WARMUP_PLAN` 里的面板一次建完（仅在**窗口仍存活且可见**时执行）。

    ★★ 2026-09-18 段错误教训（上一版）：曾用 `QTimer.singleShot` 串起 5 段链式
      延迟逐个建面板 —— 回调持裸 lambda，窗口在链中途销毁时触发 `MainWindow`
      已释放对象 → **C 级 access violation**（pytest 全绿但进程 rc=139；
      `QASCADE_NO_WARMUP=1` 对比 rc=0 确认由预热引入）。改为：单次延迟 +
      触发时先查存活 + 一次建完 + 只在窗口可见时预热（离屏窗口 close() 后不再触发）。
    """
    if not _warm_alive(window):
        return
    try:
        if not window.isVisible():
            return
    except Exception:
        return
    for _d, wattr, dattr in _WARMUP_PLAN:
        if not _warm_alive(window):
            return
        _warm_one(window, wattr, dattr)


def warm_up_panels(window, delay_ms: int = 1200) -> None:
    """启动后**后台预热**关键面板（用户定案 2026-09-18；计划见 `_WARMUP_PLAN`）。

    ★ 用户原话："能不能在打开程序的时候让 topview 在后台必须完成初始化，隐藏加载。
      同时不要造成卡顿。"

    为什么必须预热：① 调度台"调入监控区编辑"要把设备组回写为 TopView 选中，而
      TopView 是懒加载的（没打开过 → 恒为 None → 那笔构建开销会砸在**点击场景**
      这一下）；② 实测**首次懒建**耗时 topview ≈30ms / programmer ≈122ms /
      effect ≈188ms / **matrix ≈645ms** —— matrix 最贵，用户第一次点开它最卡。
      启动后空闲期建完，任何一次交互都不再付这笔。

    不卡顿的实现：延迟 `delay_ms`（让首帧 UI 先画完）→ 单次触发 → 一次建完；
    失败静默、窗口关闭即取消。`delay_ms<=0` = 同步执行（给测试用）。
    """
    from PyQt6.QtCore import QTimer
    if delay_ms <= 0:
        _warm_all(window)
        return
    t0 = QTimer(window)                    # parent=window：窗口销毁时自动取消
    t0.setSingleShot(True)
    t0.timeout.connect(lambda: _warm_all(window))
    t0.start(int(delay_ms))


__all__ = ["recall_nodes", "release_nodes", "release_one_node", "_RecallAccumulator"]
