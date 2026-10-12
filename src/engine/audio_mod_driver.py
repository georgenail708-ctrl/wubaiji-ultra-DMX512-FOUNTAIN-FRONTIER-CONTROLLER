# -*- coding: utf-8 -*-
"""音频调制 → clip 层输出的帧驱动（R174 #7 的引擎消费侧）。

机制（R182 通道 B = 唯一消费点）：`apply_channel_scope` 是纯函数，按调制值
缩放 **目标通道地址**（scope 语义：只动该范围内的通道，其它通道零改动），
由 `PlaybackEngine._apply_audio_mod_scope` 在 `_render_position` 的 outputs
定稿点回写。烘焙逐帧同样经过该点 → 调制按 pos 确定性烘进缓存（换调制参数
经运行态指纹 `_audio_mod_signature` 解锁重烘）。

通道 A（`engine._modulation_gain` 全局 others_delta 缩放）已于 R182 **关停**
（`update_modulation_gain` 只算不写），防两路叠加成双调制；`_modulation_gain`
恒中性 1.0。

调制值 = engine.audio_modulation.modulation_value(state, features, pos)；
state 来自 Project.audio_modulation_state，features 来自
audio_feature_cache（beat_this 网格，键=文件 sha256）。烘焙前 `warm_features`
同步预热，避免烘焙期 features=None → 特征项=1.0 烘错。

挂在 main_window 的 Tier0 tick 消费链上（_render_clip_layer 之前一步），
生成线程模式下由 render 前的同一入口更新——**不在其他线程碰引擎**。
"""
from __future__ import annotations

import logging
import os
from copy import deepcopy
import numpy as np
from typing import Dict, List, Optional
from .audio_mod_lifetime import suppressed

logger = logging.getLogger(__name__)

import weakref

_STATE_CACHE: dict = {"ref": None, "state": None, "raw": None}
_FEATURE_CACHE: dict = {"path": None, "stamp": None, "features": None}

def _file_stamp(path: str):
    """音频文件指纹 (mtime_ns, size)；(0, 0) = 取不到（走路径比对）。

    R182 审计修：同路径文件内容被替换（重导出/覆盖）时旧特征失效——
    修前只按路径比对，会话内会一直返回旧特征。
    """
    try:
        st = os.stat(path)
        return (int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (0, 0)
#: 上次更新的原始调制值（诊断/测试断言用；同值跳过写引擎）
LAST_VALUE: dict = {"v": None}

def _resolve_state(project) -> dict:
    """工程 → 归一化调制状态（弱引用 + **原始 state 值比对**，审计终修）。

    旧版只按 project 身份缓存——同一工程对象上**原地编辑**
    `audio_modulation_state`（r130 链和编辑大页都是这么写的）后缓存不失效，
    调制永远用旧状态。现改为：弱引用命中后**再比对原始 dict 是否同值**，
    不同值即重建。仅失效时保存独立嵌套快照；命中时不复制数据。
    """
    from src.engine.audio_modulation import normalize_state

    raw_now = getattr(project, "audio_modulation_state", {}) or {}
    ref = _STATE_CACHE.get("ref")
    if ref is not None and ref() is project:
        cached_raw = _STATE_CACHE.get("raw")
        if cached_raw is not None and cached_raw == raw_now:
            return _STATE_CACHE["state"]
    _STATE_CACHE["ref"] = weakref.ref(project)
    _STATE_CACHE["raw"] = deepcopy(raw_now)
    _STATE_CACHE["state"] = normalize_state(raw_now)
    return _STATE_CACHE["state"]

def _resolve_features(audio_path: str):
    """音频路径 → 特征（audio_feature_cache 二级缓存；缺路径给 None）。

    R178 审计 P2-8：miss 传 background_on_miss=True（现算挪后台线程，UI
    帧不再锁在特征锁内数秒）；miss 的 None **不写二级缓存** —— 否则下帧
    命中的是 None，后台算完也永远用不上（返回 None 时下帧重查，命中即生效）。
    烘焙线程走 `warm_features` 先行同步预热（现算落在烘焙线程）。
    """
    if not audio_path:
        return None
    stamp = _file_stamp(audio_path)
    if (_FEATURE_CACHE["path"] == audio_path
            and _FEATURE_CACHE.get("stamp") == stamp):
        return _FEATURE_CACHE["features"]
    try:
        from src.audio.audio_feature_cache import get_features
        feat = get_features(audio_path, background_on_miss=True)
    except Exception:
        logger.debug("audio feature resolve failed: %s", audio_path, exc_info=True)
        # A transient lookup exception is a miss, not a permanent result.
        # Bad-file/background-work de-duplication remains in get_features.
        return None
    if feat is not None:
        _FEATURE_CACHE["path"] = audio_path
        _FEATURE_CACHE["stamp"] = stamp
        _FEATURE_CACHE["features"] = feat
    return feat

def _has_modulation_sources(state):
    return bool(state and (state.get("clips") or state.get("processors")))


def warm_features(project, audio_path: str = "") -> bool:
    """烘焙前同步预热特征（R182：防"烘焙期 features=None → 特征=1.0 烘错"）。

    `get_features(..., background_on_miss=False)` 在**调用线程**现算（烘焙线程，
    非 UI 帧），算完回填两级缓存并同步进 `_FEATURE_CACHE` —— 随后烘焙逐帧走
    `apply_channel_scope → _resolve_features` 全部命中，调制值按特征确定性产出。

    仅在「工程有调制 clips」时预热（无调制不白算）。返回是否就绪（诊断/测试）。
    """
    try:
        state = _resolve_state(project) if project is not None else None
    except Exception:
        state = None
    if not _has_modulation_sources(state):
        return False
    path = str(audio_path or getattr(project, "audio_path", "") or "")
    if not path:
        return False
    try:
        from src.audio.audio_feature_cache import get_features
        feat = get_features(path, background_on_miss=False)
    except Exception:
        logger.debug("audio feature prewarm failed: %s", path, exc_info=True)
        return False
    if feat is None:
        # 失败进程内记忆（_FAILED）；不写 _FEATURE_CACHE（否则 miss 被钉死）
        return False
    _FEATURE_CACHE["path"] = path
    _FEATURE_CACHE["stamp"] = _file_stamp(path)
    _FEATURE_CACHE["features"] = feat
    return True


def update_modulation_gain(engine, project, audio_path: str, pos: float,
                           playing: bool = True) -> float:
    """保留 tick 的 LAST_VALUE 诊断；实际增益只由通道 B 消费一次。

    R182 全局 gain 恒为中性 1.0；停止态复位原诊断，防止两路叠加。
    默认诊断数学使用已挂载 CE，未挂载路径保留原始参考实现。
    """
    if not playing or suppressed(project) or getattr(project, "audio_modulation_state", {}).get("processor_mode") == "chain":
        reset_modulation_gain(engine)
        return 1.0
    state = _resolve_state(project) if project is not None else None
    value = 1.0
    if state and state.get("scope_folder_id") and state.get("clips"):
        features = _resolve_features(audio_path)
        value = _diagnostic_value(state, features, float(pos))
    LAST_VALUE["v"] = value
    return value


def _diagnostic_value(state, features, pos):
    from .native_audio_bridge import diagnostic_value
    native = diagnostic_value(state, features, pos)
    if native is not None:
        return native
    from .audio_modulation import modulation_value
    return modulation_value(state, features, pos, state_is_normalized=True)


def reset_modulation_gain(engine) -> None:
    """停止/失活时把引擎增益复位 1.0（R182 后仅作残留防线；不再有写方）。"""
    LAST_VALUE["v"] = None
    if engine is not None and getattr(engine, "_modulation_gain", 1.0) != 1.0:
        engine._modulation_gain = 1.0


# ── P0 作用域真实现（审计 C：target_ch_types 此前零消费=装饰）──────────
# 语义：调制不再走全局 _modulation_gain（那会缩放全部 clip；R182 已彻底关停
# 该写方），而是按 {ch_type: scale} 作用于 **scope 内普通轨 clip 层的目标
# 通道地址集**（唯一消费点，见 `apply_channel_scope`）。
# 地址集由工程 fixtures+defs 现算（scope 变化时失效缓存）。

_CHADDR_CACHE: dict = {"sig": None, "map": None}   # {ch_type: {(uni, addr0), ...}}
#: 目标地址下标表（{域号: np 数组}）记忆化——每帧调用安全（见 `_scope_index`）
_SCOPE_INDEX_CACHE: dict = {"map": None, "targets": None, "by_uni": {}}


def _fixture_channel_addrs(fx, d) -> List[tuple]:
    """单台设备 → [(通道类型, (universe, addr0))]（空类型跳过）。"""
    uni = int(getattr(fx, "universe", 1) or 1)
    start = int(getattr(fx, "start_channel", 1) or 1)
    out = []
    for i, ch in enumerate(getattr(d, "channels", []) or []):
        ct = str(ch or "").strip().lower()
        if ct:
            out.append((ct, (uni, start - 1 + i)))
    return out


def _fixture_addrs(out: Dict[str, set], fx, defs: Dict[str, object]) -> None:
    """单台设备的通道地址归集（helper，压 CC）。"""
    d = defs.get(str(getattr(fx, "def_id", "") or ""))
    if d is None:
        return
    for ct, addr in _fixture_channel_addrs(fx, d):
        out.setdefault(ct, set()).add(addr)


def _ch_type_addr_map(project) -> Dict[str, set]:
    """工程 → {通道类型: {(universe, addr0_0based), ...}}。

    ★ 审计 B/P1-3：裸 id(project) 键无强引用（GC 后同址串台）→ weakref+is。
    ★★ A10 审计 P0：仅 weakref+is 仍会**原地改配接后陈旧命中**（start_channel/
      universe/def_id 原地改，列表身份不变）→ 按旧地址缩放=打错通道。补
      patch_rev + 长度复核（照 clip_output_filters._mask_cache 正确范式）。
    """
    fixtures = list(getattr(project, "fixtures", []) or [])
    rev = int(getattr(project, "patch_rev", 0) or 0)
    ref = _CHADDR_CACHE.get("sig")
    if (isinstance(ref, weakref.ref) and ref() is project
            and _CHADDR_CACHE["map"] is not None
            and _CHADDR_CACHE.get("rev") == rev
            and _CHADDR_CACHE.get("n") == len(fixtures)):
        return _CHADDR_CACHE["map"]
    out: Dict[str, set] = {}
    defs = {str(getattr(d, "id", "") or ""): d
            for d in (getattr(project, "fixture_defs", []) or [])}
    for fx in fixtures:
        _fixture_addrs(out, fx, defs)
    _CHADDR_CACHE["sig"] = weakref.ref(project)
    _CHADDR_CACHE["map"] = out
    _CHADDR_CACHE["rev"] = rev
    _CHADDR_CACHE["n"] = len(fixtures)
    return out


def invalidate_caches() -> None:
    """工程切换/清空：调制状态、特征、地址映射、Draw 规则滤波状态全部失效。"""
    # ★ 审计 B/P1-2 修正：真键是 "ref"（弱引用）；"proj_id" 是早版残留
    _STATE_CACHE["ref"] = None
    _STATE_CACHE["raw"] = None
    _STATE_CACHE["state"] = None
    _FEATURE_CACHE["path"] = None
    _FEATURE_CACHE["features"] = None
    _CHADDR_CACHE["sig"] = None
    _CHADDR_CACHE["map"] = None
    _SCOPE_INDEX_CACHE.update(map=None, targets=None, by_uni={}, ct=None)
    # ★ R183 M2/3.4b + 清查⑦A P1-6：规则滤波/多灯展开/scope 识别缓存一并清
    try:
        from src.engine.audio_mod_draw_compile import clear_caches
        from src.engine.audio_mod_scope import invalidate_scope_cache
        from src.engine.audio_mod_spread import invalidate_caches as _spread_clear
        clear_caches()
        _spread_clear()
        invalidate_scope_cache()
    except Exception:
        logger.debug("draw rule filter cache clear failed", exc_info=True)


def apply_channel_scope(outputs: Dict[int, tuple], project,
                        audio_path: str, pos: float,
                        base_gain: float = 1.0) -> Dict[int, tuple]:
    """clip 层帧 → 按调制目标通道缩放后的帧（P0 作用域真实现）。

    `outputs` = {universe: (data u8, active bool)}；返回同构新 dict（不改输入）。
    规则：
      - 无启用调制/scope 为空/无 clips → 原样返回（零开销路径）。
      - scale=modulation_value(state, features, pos)（0..1）；
      - 只缩放**目标通道类型的地址**（全局 target_ch_types ∪ 各 clip targets；
        显式全空 → scope 识别通道回退，见 `_scope_addresses`）；
      - 缩放作用于 delta 语义：new = base_gain × (anchor + (val-anchor)×scale)
        的近似（val × scale）——调制值即该通道的增益系数。
    R182 Draw 逐通道规则：state["rules"] 非空时，可覆盖通道走
    `audio_mod_draw_compile.scale_map`（含源卡特征/相位与每卡 amount），
    未覆盖通道沿用上式全局值；rules 为空 = 旧全局口径逐字节不变。
    """
    state = _resolve_state(project) if project is not None else None
    if suppressed(project) or not state or state.get("processor_mode") == "chain" or not state.get("scope_folder_id"):
        return outputs
    has_clips = bool(state.get("clips"))
    has_rules = bool(state.get("rules"))
    if not has_clips and not has_rules:
        return outputs
    by_uni = _scope_index(project, state)
    if not by_uni:
        return outputs
    return _scoped_outputs(outputs, {"project": project, "state": state,
                                     "audio_path": audio_path, "pos": pos,
                                     "by_uni": by_uni})


def _scoped_outputs(outputs, ctx: dict):
    """逐域绝对增益：未展开走原全局/规则，展开走逐灯 clip/规则桶。"""
    from .native_audio_bridge import scoped_outputs
    native = scoped_outputs(outputs, ctx)
    if native is not None:
        return native
    from src.engine import audio_mod_fan
    from src.engine.audio_modulation import modulation_value
    state, pos = ctx["state"], ctx["pos"]
    features = _resolve_features(ctx["audio_path"])
    # 纯规则图（无 clips）规则全程生效；有 clips 时区段外 env=0 → 规则静默。
    t = float(pos)
    scale = (modulation_value(state, features, t, state_is_normalized=True)
             if state.get("clips") else 1.0)
    # ★ 展开计划先于恒等快速路径：scale 恰 1.0（波峰）时逐灯值仍可不同，
    # 快速路径只在展开未启用时成立（R183 3.4b）。
    fan = audio_mod_fan.plan(state, ctx["project"], ctx["by_uni"], features, t,
                             targets=_rule_targets(state))
    if fan is None and scale >= 1.0 and not state.get("rules"):
        return outputs   # 全量=不缩放（恒等快速路径；规则空且无展开时适用）
    per_ct = None if fan is not None else _rule_scales(state, features, t)
    if fan is None and not per_ct and scale >= 1.0:
        return outputs
    return {uni: _scale_universe(entry, ctx["by_uni"].get(int(uni)), scale,
                                 _ct_index(ctx["project"], state, int(uni)),
                                 per_ct,
                                 fan=None if fan is None
                                 else audio_mod_fan.view(fan, int(uni)))
            for uni, entry in outputs.items()}


def _rule_scales(state: dict, features, pos: float) -> Dict[str, float]:
    """Draw 规则卡 → {通道类型: scale}（无 rules/异常 → 空表=旧口径）。

    env = `_active_envelope_norm`。区段语义（R183 波5 口径修正）：有 clips 时
    pos 在区段外 env=0 → 空表回落全局基线（规则只随区段生效）；无 clips 才
    恒 1.0 全程生效。规则卡再乘"卡包络 × amount × 源信号"。
    """
    if not state.get("rules"):
        return {}
    try:
        from src.engine.audio_modulation import _active_envelope_norm
        from src.engine.audio_mod_draw_compile import scale_map
        env = (_active_envelope_norm(state, float(pos))
               if state.get("clips") else 1.0)
        if env <= 0.0:
            return {}
        # ★ R182 审计修：全局「调制量」进规则路径。语义与 clips 路径一致——
        # amount 是增益系数（0=静音该通道），由 scale_map 乘进标量。
        amount = float(state.get("amount", 1.0) or 0.0)
        return scale_map(state, _rule_targets(state), features, float(pos), env,
                         amount=amount)
    except Exception:
        logger.debug("draw rule scale failed", exc_info=True)
        return {}


def _rule_targets(state) -> tuple:
    """规则卡目标类型（显式 target_ch_types 优先，空则取 rules 里出现的类型）。"""
    targets = [str(t) for t in (state.get("target_ch_types") or []) if str(t)]
    if targets:
        return tuple(targets)
    return tuple(sorted({str(r.get("ch_type", ""))
                         for r in (state.get("rules") or ())
                         if isinstance(r, dict) and str(r.get("ch_type", ""))}))


def _ct_index(project, state, universe: int) -> Dict[str, "np.ndarray"]:
    """单域：{通道类型: 目标地址 0 基下标数组}（rules 非空才建，缓存同 map 身份）。"""
    if not state.get("rules"):
        return {}
    targets = _rule_targets(state)
    ch_map = _ch_type_addr_map(project)
    cached = _SCOPE_INDEX_CACHE.get("ct")
    if (isinstance(cached, dict) and cached.get("map") is ch_map
            and cached.get("targets") == targets):
        return cached["by_uni"].get(int(universe), {})
    by_uni: Dict[int, Dict[str, list]] = {}
    for ct in targets:
        for (u, a0) in ch_map.get(str(ct).strip().lower(), ()):
            if 0 <= a0 < 512:
                by_uni.setdefault(int(u), {}).setdefault(str(ct), []).append(int(a0))
    out = {u: {ct: np.asarray(sorted(v), dtype=np.int64) for ct, v in d.items()}
           for u, d in by_uni.items()}
    _SCOPE_INDEX_CACHE["ct"] = {"map": ch_map, "targets": targets, "by_uni": out}
    return out.get(int(universe), {})


def _scale_universe(entry, idx, scale: float, ct_idx=None, per_ct=None, *,
                    fan=None):
    """单域缩放（向量化）：目标地址下标数组乘 scale（坏条目原样返回）。

    R181 向量化：旧的逐地址 Python 循环在"全范围回退=数千地址 × 多域"时
    每帧 4ms+；numpy 花式索引把同工作量降到常数级开销（取整口径与
    `int(round(v*scale))` 一致——两者都是银行家舍入）。

    R182：`per_ct` 有值时逐通道类型覆盖（规则卡 scale 优先，未覆盖通道
    仍用全局 `scale`）；`ct_idx` 是单域 {通道类型: 地址下标数组}。
    R183 3.4b：`fan` 非空（多灯展开）时走逐地址数组（clip 逐灯值 + 规则逐桶
    绝对覆盖已折叠其中，见 `audio_mod_fan.scales`）。展开未启用时调用方不传
    `fan` → 本函数旧分支**逐位不变**。
    """
    if idx is None:
        return entry
    try:
        data, active = entry
        nd = np.asarray(data).copy()
    except Exception:
        return entry
    valid = idx[idx < nd.size]
    if fan is not None:
        return (_fan_scale_frame(nd, idx, valid, (scale, ct_idx, fan)), active)
    if valid.size:
        nd[valid] = np.rint(nd[valid].astype(np.float64) * scale).astype(np.uint8)
    if per_ct and ct_idx:
        _overlay_ct_scales(nd, data, ct_idx, per_ct, scale)
    return (nd, active)


def _fan_scale_frame(nd, idx, valid, frame: tuple):
    """展开路径单域缩放正文（tuple 传参压位置参数数）：返回 nd。"""
    scale, ct_idx, fan = frame
    from src.engine.audio_mod_fan import scales as _fan_scales
    pa = _fan_scales(scale, idx, ct_idx, fan)
    if valid.size and pa is not None:
        nd[valid] = np.rint(nd[valid].astype(np.float64)
                            * pa[idx < nd.size]).astype(np.uint8)
    return nd


def _overlay_ct_scales(nd, orig, ct_idx: Dict[str, "np.ndarray"],
                       per_ct: Dict[str, float], global_scale: float) -> None:
    """规则卡逐通道覆盖：从**原始帧**重算"有规则"的地址（覆盖全局结果）。

    必须用原始值（不是已被全局 scale 乘过的 nd）：规则标量是绝对增益，
    在已缩放值上再乘会得到 scale×rule 的双重乘积（实测 200·0.5·1.0=100
    而非 200）。
    """
    for ct, idxs in ct_idx.items():
        sc = per_ct.get(str(ct))
        if sc is None or sc == global_scale:
            continue
        valid = idxs[idxs < nd.size]
        if valid.size:
            nd[valid] = np.rint(np.asarray(orig)[valid].astype(np.float64)
                                * sc).astype(np.uint8)


def _scope_addresses(project, state) -> set:
    """调制目标地址集 = 全局 target_ch_types ∪ 各 clip targets 的地址并集。

    R181：显式目标全空时回退 **scope 识别通道**（collect_scope_channels_from_project）
    ——用户规格「范围内所有录制 clip 的通道类型都是识别与调制对象」，没有显式
    勾选时全范围即目标；否则空目标态下 addrs 为空、调制作用域无人可调。
    """
    addrs = set()
    for idx in _scope_index(project, state).values():
        addrs.update((int(u), int(a)) for u, a in idx)
    return addrs


def _scope_target_list(project, state) -> tuple:
    """本次调制的目标通道类型（显式优先，空则 scope 识别回退；有序去重）。"""
    targets = _explicit_targets(state)
    if targets:
        return tuple(sorted(str(t) for t in targets if str(t)))
    return tuple(_scope_fallback_targets(project, state))


def _scope_index(project, state) -> Dict[int, "np.ndarray"]:
    """{域号: 目标地址 0 基下标 np 数组}（按 映射表身份 + 目标表 记忆化）。

    每帧调用安全：命中路径只做两次 `is`/等值比对；地址表本身仍由
    `_ch_type_addr_map` 的工程弱引用缓存持有，工程换了即整体重建。
    """
    ch_map = _ch_type_addr_map(project)
    targets = _scope_target_list(project, state)
    cached = _SCOPE_INDEX_CACHE.get("map")
    if (cached is not None and cached is ch_map
            and _SCOPE_INDEX_CACHE.get("targets") == targets):
        return _SCOPE_INDEX_CACHE["by_uni"]
    addrs: set = set()
    for ct in targets:
        addrs.update(ch_map.get(str(ct).strip().lower(), ()))
    by_uni: Dict[int, list] = {}
    for (u, a0) in addrs:
        if 0 <= a0 < 512:
            by_uni.setdefault(int(u), []).append(int(a0))
    out = {u: np.asarray(sorted(v), dtype=np.int64) for u, v in by_uni.items()}
    _SCOPE_INDEX_CACHE.update(map=ch_map, targets=targets, by_uni=out)
    return out


def _explicit_targets(state) -> set:
    """状态里的显式目标：全局 target_ch_types ∪ 各 clip targets。"""
    targets = set(state.get("target_ch_types") or [])
    for c in state.get("clips") or []:
        targets.update(c.get("targets") or [])
    return targets


def _scope_fallback_targets(project, state) -> List[str]:
    """显式 targets 全空 → 按 scope 识别通道回退（异常给空=退回旧恒等行为）。"""
    try:
        from src.engine.audio_mod_scope import collect_scope_channels_from_project
        return collect_scope_channels_from_project(
            project, str(state.get("scope_folder_id", "") or ""))
    except Exception:
        logger.debug("scope channel fallback failed", exc_info=True)
        return []
