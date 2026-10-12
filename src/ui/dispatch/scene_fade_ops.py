# -*- coding: utf-8 -*-
"""场景**淡入淡出**的接线层：算"该场景正在输出哪一层"，并把它交给 `FadeRunner`。

用户 2026-09-14：
  "可以设置自动的淡入淡出时间，这样即使是 flash 模式也能轻柔。"
  "淡入淡出时间这个跟哪种模式无关，flash 还是 latch 都可以生效。"
  "这个是独立的新增功能也就是说即使不绑定按键，我鼠标点击一样生效。"

纯算法在 `scene_fade.FadeRunner`；本模块只负责"把面板侧的场景对象翻译成层的记账"。
从 `store_area_ops` 拆出（该文件此前触门禁 500 行线）。依赖是**单向**的：
本模块只向下依赖 `FadeRunner` 与场景缓存，`store_area_ops` 反过来 import 本模块。
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def _cache_or_none(cid: str):
    """按 id 取场景（取不到 None，绝不抛）。"""
    try:
        from ...processing.dispatch import get_effect_cache_store
        return get_effect_cache_store().get(str(cid))
    except Exception:
        # R181 P2-3：场景缓存不可用 → 层解析全空（淡化静默失效），留痕
        _log.debug("场景缓存不可用 cid=%s", cid, exc_info=True)
        return None


def _add_unique(out: list, value) -> None:
    """非空且未出现过则追加（保序去重）。"""
    sv = str(value or "")
    if sv and sv not in out:
        out.append(sv)


def _node_dicts(cid: str) -> list:
    """该场景的全部节点 dict（取不到给空列表）。"""
    try:
        c = _cache_or_none(cid)
        return [dict(e.get("node", {}) or {})
                for e in (getattr(c, "nodes", []) or [])]
    except Exception:
        return []


#: 节点**自报**层 → 该面板**真正写出**的层（只列不一致的那一对）。
#: ★★ 2026-09-18 回访修正（"两处独立算同一个键 → 打不中"的同型实例）：
#:   programmer 的节点自报 `layers=["programmer"]`，而场景 recall / 用户操作**只落
#:   `fader`**（同一条对应关系见 `scene_takeover._OUTPUT_LAYERS` 的说明与
#:   `_PRIMARY_LAYER`）。于是淡化增益被施加在一个**空的** legacy 层上：
#:   实测（场景 pump=200、淡化 1.0s，真实路径点场景再点关闭）——输出整整 1 秒
#:   纹丝不动地停在 200，然后**硬切**到 0（0.96s 内 6 个采样全 200）。
#:   即"设了淡化时间却完全没有淡入淡出"。这里把自报层翻译成真层。
_DECLARED_LAYER_FIXUPS = {"programmer": "fader"}


def _declared_layers(cid: str) -> list:
    """节点自报的层（**已按 `_DECLARED_LAYER_FIXUPS` 翻译成真实落层**；去重保序）。"""
    out = []
    for nd in _node_dicts(cid):
        for lay in (nd.get("layers", []) or []):
            s = str(lay or "")
            if s:
                _add_unique(out, _DECLARED_LAYER_FIXUPS.get(s, s))
    return out


#: 面板 → **主混音层**（淡化兜底用）。只取每面板的"主层"，刻意不取同面板的所有层。
#: ★ 深查修正 M4：原实现把"该面板名下的**所有**层"都拿去淡化（effect 会一次性占
#:   effect/effect_auto/ml_lab 三层）。多场景共用这些层时，一个场景淡出就会把别的
#:   场景在同一层上的输出一起压暗。宁可少淡一层，也绝不能误伤别的场景 ——
#:   正常路径走节点自报的 `layers`，本表只服务旧工程。
_PRIMARY_LAYER = {
    "programmer": "fader", "effect": "effect", "matrix": "matrix",
    "audio": "audio", "joystick": "joystick", "linker": "linker",
    "clip": "clip_traj",
}


def _layers_from_panels(cid: str) -> list:
    """兜底：按节点所属面板反查该面板的**主**层（`_PRIMARY_LAYER` 单一真源）。"""
    out = []
    for nd in _node_dicts(cid):
        _add_unique(out, _PRIMARY_LAYER.get(str(nd.get("panel", "") or "")))
    return out


def _scene_layers(self, cid: str) -> list:
    """该场景涉及的**混音层**（以节点自报 `layers` 为准，别名翻译见 fixup 表）。

    淡化必须作用在"这个场景正在输出哪一层"：自报层与面板真实落层不一致的
    （programmer）按 `_DECLARED_LAYER_FIXUPS` 翻译，否则增益打在空层上 = 淡化失效。
    """
    layers = _declared_layers(cid)
    return layers or _layers_from_panels(cid)


def _fade_runner(self):
    """懒建淡化推进器（NM 由面板 `_nm_provider` 注入）。"""
    r = getattr(self, "_fade_runner", None)
    if r is None:
        from .scene_fade import FadeRunner
        r = FadeRunner(lambda: (self._nm_provider() if self._nm_provider else None), self)
        self._fade_runner = r
        # ★ R194/S2-02：登记进销毁安全网的 cell（wire_zone_teardown 捕获），
        #   否则 destroyed 时 reset() 拿到 None，层增益残留 = 该层永久静音。
        cell = getattr(self, "_fade_runner_cell", None)
        if cell is not None:
            cell[0] = r
    return r


def _fade_in_scene(self, c) -> bool:
    """登记该场景为点亮（`secs>0` 则淡入）。返回是否真在跑斜坡。

    ★ 即使**没有**淡化时间也要登记：FadeRunner 需要知道"谁正亮着这一层"，
      别的场景淡出时才不会把它一起压暗（深查 H3）。返回 False 只表示"瞬时到位"。
    """
    try:
        secs = float(c.fade_seconds()) if callable(getattr(c, "fade_seconds", None)) else 0.0
    except Exception:
        secs = 0.0
    cid = str(getattr(c, "id", "") or "") if c is not None else ""
    layers = _scene_layers(self, cid)
    if not cid or not layers:
        return False
    try:
        # ★ 以**场景 id 为键**：同一键映射多个场景 / 多场景共用一层时互不干扰
        _fade_runner(self).activate(cid, layers, secs)
        return secs > 0.0
    except Exception:
        # R181 P2-3：登记淡入失败 → 该场景不参与"谁还亮着"记账（H3 误伤前提）
        _log.debug("场景淡入登记失败 cid=%s（别的场景淡出可能误伤本层）", cid,
                   exc_info=True)
        return False


def _fade_out_scene(self, c, on_done) -> bool:
    """按场景的淡化秒数淡出，结束回调 `on_done`；未设时间返回 False。"""
    try:
        secs = float(c.fade_seconds()) if callable(getattr(c, "fade_seconds", None)) else 0.0
    except Exception:
        secs = 0.0
    cid = str(getattr(c, "id", "") or "")
    if secs <= 0.0:
        _fade_runner(self).drop(cid)      # 无淡化：直接归还记账（调用方走瞬时释放）
        # ★ R181 P2-4：drop 已归还 scale → 推杆窗滑杆同步归 100（审计 M2 失同步）
        _reset_takeover_windows(self, [cid])
        return False
    layers = _scene_layers(self, cid)
    if not layers:
        return False
    try:
        return bool(_fade_runner(self).deactivate(cid, layers, secs, on_done=on_done))
    except Exception:
        # R181 P2-3：启动淡出失败 → 调用方会走瞬时释放，但释放回调可能不触发
        _log.debug("场景淡出启动失败 cid=%s（本次按瞬时释放处理）", cid, exc_info=True)
        return False


def _reset_takeover_windows(panel_or_self, cids=None) -> None:
    """把推杆窗滑杆归 100（与 scale 归还同步；R181 P2-4 接线，审计 M2）。

    `cids` 为 None 时复位该面板**所有**已知推杆窗（全局清台/切工程路径）。
    """
    try:
        from .fader_takeover import _ATTR, reset_takeover_window
    except Exception:
        return
    wins = getattr(panel_or_self, _ATTR, None)
    if not isinstance(wins, dict):
        return
    keys = list(wins) if cids is None else [str(c) for c in cids]
    for cid in keys:
        reset_takeover_window(panel_or_self, cid)


def _drop_scene_fade(self, cid: str) -> None:
    """丢弃某场景的淡化斜坡（场景被删除/覆盖时调用，防增益残留永久压暗该层）。

    ★ R181 P2-4：`drop` 同时归还该场景的推杆 scale（`FadeRunner.drop`）→ 推杆窗
      滑杆必须同步归 100，否则 UI 显示 30% 而实际已按 100% 出光（审计 M2 失同步）。
    """
    try:
        r = getattr(self, "_fade_runner", None)
        if r is not None:
            r.drop(str(cid or ""))
    except Exception:
        # R181 P2-3：删除/覆盖时丢弃斜坡失败 → 残留增益可能永久压暗该层
        _log.debug("丢弃场景淡化斜坡失败 cid=%s（增益残留可能压暗该层）", cid,
                   exc_info=True)
    _reset_takeover_windows(self, [cid])


def _reset_scene_fades(self) -> None:
    """归还**全部**场景的淡化记账（全局清台 / 切工程 / 面板销毁时调用）。

    清台后没有任何场景还亮着，若记账不清空，残留的 0.0 归属会让该层在后续手动编辑
    时依旧被压暗（深查 A3：`_layer_gains` 原本没有任何复位入口）。

    ★ R181 P2-4：`FadeRunner.reset` 归还全部 scale → 全部推杆窗滑杆同步归 100。
    """
    try:
        r = getattr(self, "_fade_runner", None)
        if r is not None:
            r.reset()
    except Exception:
        # R181 P2-3：全量归还失败 → 切工程/清台后旧增益残留（层被永久压暗）
        _log.debug("全量归还场景淡化记账失败（旧增益可能残留）", exc_info=True)
    _reset_takeover_windows(self)


__all__ = ["_cache_or_none", "_add_unique", "_scene_layers", "_fade_runner",
           "_fade_in_scene", "_fade_out_scene", "_drop_scene_fade",
           "_reset_scene_fades", "_reset_takeover_windows",
           "_layers_from_panels", "_declared_layers"]
