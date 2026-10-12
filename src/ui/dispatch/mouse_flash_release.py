# -*- coding: utf-8 -*-
"""★ R183 T8 / 审计⑦B：**鼠标按住的 flash 失焦兜底释放**。

从 `store_area_ops` 拆出（该文件是既有的大文件，R183 新增让它越过 500 行上限；
按项目纪律"新逻辑落新模块"外提，语义归属不变 —— 仍由 `AREA_HANDLERS` 装配）。

问题（与键盘 M1 条款同类）：flash 是"按住才亮"。鼠标按住卡片时切走窗口
（Alt+Tab / 弹窗抢焦点 / 远程桌面断开），Qt 不再派发 MouseRelease →
`slot_released` 永不触发 → 该场景**永久卡在激活**且占着输出（用户只能靠
"关闭该场景"手工收拾；若卡片被换页/过滤隐藏则连入口都没了）。
键盘路径已有 M1 兜底（`SceneKeyFilter.release_held`），鼠标路径此前**完全没有**。

接线：`scene_key_filter.eventFilter` 收到失活事件时调面板的
`_release_mouse_flash()`（与 `release_held` 并列），两路合成
"失焦 = 释放全部按住" 的完整语义。
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

#: 面板上"鼠标正按住"的 flash 场景 id 集合（失焦释放的判据）
_MOUSE_HELD_ATTR = "_mouse_flash_held"


def _mouse_held_set(panel) -> set:
    """取（必要时创建）面板的"鼠标按住 flash"集合（销毁期返回空集，绝不抛）。"""
    try:
        s = getattr(panel, _MOUSE_HELD_ATTR, None)
    except Exception:
        return set()
    if not isinstance(s, set):
        s = set()
        try:
            panel.__dict__[_MOUSE_HELD_ATTR] = s
        except Exception:
            return s
    return s


def mouse_held_add(panel, cid: str) -> None:
    """登记"鼠标按住了某个 flash 场景"（`_on_store_pressed` 调）。"""
    if cid:
        _mouse_held_set(panel).add(str(cid))


def mouse_held_discard(panel, cid: str) -> None:
    """销记（正常松手时调；防失焦时重复释放一个早已放掉的场景）。"""
    _mouse_held_set(panel).discard(str(cid or ""))


def release_mouse_flash(panel) -> int:
    """窗口/应用失活时把**鼠标按住的** flash 全部按点动语义释放（返回释放数）。

    幂等：集合在释放时清空，重复调用不再触发（被释放场景自身的 `_is_active_flash`
    守卫也保证"已释放的不重复释放"）。
    """
    held = _mouse_held_set(panel)
    if not held:
        return 0
    ids = sorted(held)
    held.clear()
    from .store_area_ops import _on_store_released   # 环：函数级延迟导入
    n = 0
    for cid in ids:
        try:
            _on_store_released(panel, cid)
            n += 1
        except Exception:
            # R181 P2-3：失焦兜底释放失败 → 该场景可能永久卡在激活
            _log.debug("失焦兜底释放失败 cid=%s（该场景可能仍卡在激活）", cid,
                       exc_info=True)
    if n:
        try:
            panel._status.setText(f"窗口失焦：已兜底释放 {n} 个按住的 flash")
        except Exception:
            _log.debug("失焦释放状态栏文案失败", exc_info=True)
    return n


def _release_mouse_flash(panel) -> int:
    """面板方法壳（下划线前缀，供失焦过滤器按名调用）。"""
    return release_mouse_flash(panel)


__all__ = ["mouse_held_add", "mouse_held_discard", "release_mouse_flash",
           "_release_mouse_flash"]
