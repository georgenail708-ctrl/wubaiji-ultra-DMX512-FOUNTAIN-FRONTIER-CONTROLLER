# -*- coding: utf-8 -*-
"""★ R183 审计⑦B P0-3：调度台场景库变更的**撤销条目**生产器（零无关回滚）。

问题（审计实锤）：`DOMAIN_DISPATCH` 在 `project_manager.py:152` 定义后**全仓零
生产方** —— 场景的增/删/改从不产生自己的撤销条目。于是用户撤任何**别**的改动
（例如"加轨道"，它是 domain=None 的全量快照）时，那个快照把 `dispatch_state`
一并带回"库还空着"的时刻；而 `undo_restore.restore_stores_from_project` 对
dispatch_state 是**整体替换**（空 dict → 空库）→ 刚保存的场景从库与界面一起
消失（实测复现：加轨道 → 存场景A → Ctrl+Z → 库=[]、卡片=0）。

修法（审计推荐①：建卡/改卡/删卡/改模式入口先压域快照）——本模块把"入口先压
快照"做成**装饰器 + 名单**，在 `_install_store_handlers` 装配时统一套上：
  · 变异**前**取库载荷（`to_dict()`）备用；
  · 调用原方法（可能弹对话框被取消 → 库没变 → **不留空条目**）；
  · 变异后比对指纹，真的变了才把"改之前"的载荷临时注入 project.dispatch_state
    并压一条 `DOMAIN_DISPATCH` 轻量快照（`_snapshot_scoped`，剥帧，亚秒级）。

为什么用"临时注入 + 回填"而不是直接 `pm._snapshot(domain=...)`：快照 API 记的是
**当前工程状态**，而此刻库已经变了 —— 想让条目内容是"改之前"，只能先把旧载荷
放回工程、拍完再放回新载荷。这一步在**我的域内**（dispatch）完成，不动上帝类
`main_window`（其 `_on_dispatch_cache_changed` 仍是 dispatch_state 唯一写入方）。

成本：每次菜单动作一次 `to_dict()`（库空时是 `{"version":1,"caches":[]}`，可忽略；
大库实测单次 10~50ms）—— 只发生在用户显式点击动作上，不在热路径。
"""
from __future__ import annotations

import logging
from typing import List, Optional

_log = logging.getLogger(__name__)

#: 需要"改前压快照"的入口方法名（建卡/改卡/删卡/改模式/换位/改页/跑灯/变式）。
MUTATING_HANDLERS: tuple = (
    "_do_save",              # 新建场景（点空槽 / 保存选中）
    "_on_update_cache",      # 覆盖更新
    "_on_delete_caches",     # 多选删除
    "_scene_delete",         # 单卡删除
    "_scene_prompt_bound",   # 变式 / 重命名 / 备注
    "_scene_mode",           # 触发模式
    "_on_store_reordered",   # 拖拽换位
    "_bind_key",             # 键盘绑定
    "_set_fade",             # 淡入淡出
    "_set_priority",         # 优先级
    "_on_area_mode",         # 框选批量改模式
    "_on_area_bind_key",     # 框选批量绑键
    "_on_area_fade",         # 框选批量淡化
    "_chase_write",          # 跑灯序列
    "_move_to_page",         # 移动到页
    "_copy_to_page",         # 复制到页
)


def project_manager_of(panel):
    """取 ProjectManager：面板属性 → 进程内单例（与 `TimelineWidget._get_pm` 同源）。

    ★ 为什么需要单例兜底：面板由 MainWindow 以 `set_services` 注入各种 provider，
      但**没有任何一处注入 PM 本身**（R183 审计⑦B 实测：面板属性里一个都没有）。
      单例是既有约定入口（各面板/时间线都这么取），故这里也用它。
    """
    for obj in _panel_candidates(panel):
        if obj is not None:
            return obj
    try:
        from ...core.project_manager import ProjectManager
        obj = ProjectManager.instance()
        return obj if obj is not None and hasattr(obj, "_snapshot") else None
    except Exception:
        return None


def _panel_candidates(panel):
    """面板上可能的 PM 持有点（属性 + provider），逐个产出可用者。"""
    for attr in ("_pm", "pm", "_project_manager"):
        yield from _yield_pm(_safe_attr(panel, attr))
    for attr in ("_pm_provider", "_project_manager_provider"):
        fn = _safe_attr(panel, attr)
        if callable(fn):
            yield from _yield_pm(_call_safe(fn))


def _yield_pm(obj):
    """是可用 PM 才产出（否则什么都不产出）。"""
    if obj is not None and hasattr(obj, "_snapshot"):
        yield obj


def _call_safe(fn):
    """调 provider（异常 → None）。"""
    try:
        return fn()
    except Exception:
        return None


def _safe_attr(obj, name):
    """安全取属性（属性访问器自身抛异常时也返回 None，不只兜 AttributeError）。"""
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _store_payload() -> Optional[dict]:
    """当前库的完整载荷（含 releases，与 `_on_dispatch_cache_changed` 同键集）。"""
    try:
        from ...processing.dispatch import get_effect_cache_store, get_release_registry
        return {**get_effect_cache_store().to_dict(),
                "releases": get_release_registry().to_dict()}
    except Exception:
        return None


def _fingerprint() -> tuple:
    """库的廉价指纹（条数 + id/位置/内容指纹）——判"这次调用有没有真的改库"。"""
    try:
        from ...processing.dispatch import get_effect_cache_store
        store = get_effect_cache_store()
        return tuple(sorted(
            (str(getattr(c, "id", "")), int(getattr(c, "position", 0) or 0),
             int(getattr(c, "page", 0) or 0), str(getattr(c, "name", "")),
             str(getattr(c, "mode", "")), str(getattr(c, "key_binding", "")),
             float(getattr(c, "fade_in", 0.0) or 0.0),
             int(getattr(c, "priority", 0) or 0),
             str(getattr(c, "note", "")),
             len(getattr(c, "nodes", []) or []),
             str(getattr(c, "chase", "") or ""))
            for c in store.list_caches()))
    except Exception:
        return ()


def commit_dispatch_change(panel, payload_before: Optional[dict]) -> bool:
    """一次库变更的收口：压"改之前"的域快照 + 把**新载荷**写回工程。

    ① 压快照：临时把旧载荷写回 `project.dispatch_state` → `_snapshot_scoped(domain)`
       → 回填（保证"拍快照"本身不改工程内容）。
    ② 写回新载荷：撤销/重做链的两端都在 `project.dispatch_state` 上，若这里不写，
       快照记的"改之后"就是空的 → **重做会落回空库**（自测抓到的真坑：没有主窗
       监听 `cacheChanged` 时（单测/嵌入式）工程字段永远停在旧值）。
       生产路径里 `main_window._on_dispatch_cache_changed` 也会写同一份内容 ——
       两处写的是**同一份载荷**（都从 store.to_dict() 现取），幂等无副作用。
    """
    ok = snapshot_dispatch_before(panel, payload_before)
    after = _store_payload()
    if after:
        pm = project_manager_of(panel)
        proj = _project_of(pm)
        if proj is not None:
            try:
                proj.dispatch_state = after
                if hasattr(pm, "_mark_dirty"):
                    pm._mark_dirty()
            except Exception:
                _log.debug("库变更后写回 dispatch_state 失败", exc_info=True)
    return ok


def snapshot_dispatch_before(panel, payload_before: Optional[dict]) -> bool:
    """把"改之前"的库载荷压成一条 `DOMAIN_DISPATCH` 撤销条目。返回是否压栈。

    实现：临时把旧载荷写回 `project.dispatch_state` → `_snapshot_scoped(domain)`
    → 回填调用方当前载荷（保证"拍快照"这个动作本身不改工程内容）。
    """
    pm = project_manager_of(panel)
    proj = _project_of(pm)
    if pm is None or proj is None or not payload_before:
        return False
    from ...core.project_manager import DOMAIN_DISPATCH
    try:
        current = dict(getattr(proj, "dispatch_state", {}) or {})
    except Exception:
        return False
    try:
        proj.dispatch_state = dict(payload_before)
        if hasattr(pm, "_snapshot_scoped"):
            pm._snapshot_scoped(domain=DOMAIN_DISPATCH)
        else:
            pm._snapshot(domain=DOMAIN_DISPATCH)
        return True
    except Exception:
        _log.debug("调度台域快照失败（本次变更将不可撤销）", exc_info=True)
        return False
    finally:
        try:
            proj.dispatch_state = current
        except Exception:
            _log.debug("调度台域快照回填失败", exc_info=True)


def _project_of(pm):
    """取 PM 持有的 Project（`project` 属性优先，退路私有字段；取不到 None）。"""
    if pm is None:
        return None
    return _safe_attr(pm, "project") or _safe_attr(pm, "_project")


def wrap_mutating(cls, names: List[str] | tuple = MUTATING_HANDLERS) -> int:
    """给 `cls` 的名单方法套"改前压快照"（幂等）。返回套上的方法数。

    · 库没变（对话框被取消 / 校验失败）→ **不留**任何撤销条目；
    · 面板没有 PM（纯后端单测）→ 只跳过快照，方法照常执行（功能优先）。
    """
    wrapped = 0
    for name in names:
        fn = getattr(cls, name, None)
        if not callable(fn) or getattr(fn, "_dispatch_undo_wrapped", False):
            continue
        setattr(cls, name, _make_wrapper(fn))
        wrapped += 1
    return wrapped


def _make_wrapper(fn):
    """把单个入口方法包成"先取旧载荷 → 调原方法 → 变了才压域快照 + 写回新载荷"。

    ★ 重入守卫（必需）：`_chase_configure`（UI 入口）内部会调 `_chase_write`
      （程序化入口），两者都在名单里 → 不加守卫就会为**同一次用户动作**压两条
      条目（第一条等价于"没改"）→ 用户按 Ctrl+Z 觉得"没反应"（R100 同款老坑）。
      守卫让嵌套调用只由**最外层**记一次账，语料与直觉一致。
    """
    def _wrapped(self, *args, **kwargs):
        if getattr(self, "_dispatch_undo_depth", 0) > 0:
            return fn(self, *args, **kwargs)      # 嵌套：外层已负责记账
        before_payload = _store_payload()
        before_fp = _fingerprint()
        self.__dict__["_dispatch_undo_depth"] = 1
        try:
            result = fn(self, *args, **kwargs)
        finally:
            self.__dict__["_dispatch_undo_depth"] = 0
        try:
            if _fingerprint() != before_fp:
                commit_dispatch_change(self, before_payload)
        except Exception:
            _log.debug("场景库变更后压域快照失败", exc_info=True)
        return result
    _wrapped.__name__ = getattr(fn, "__name__", "wrapped")
    _wrapped.__doc__ = getattr(fn, "__doc__", None)
    _wrapped._dispatch_undo_wrapped = True
    return _wrapped


__all__ = ["MUTATING_HANDLERS", "project_manager_of", "snapshot_dispatch_before",
           "commit_dispatch_change", "wrap_mutating"]
