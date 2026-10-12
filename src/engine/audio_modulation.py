# -*- coding: utf-8 -*-
"""音频调制轨道核心逻辑（R174 #7，纯逻辑无 Qt）。

用户规格：
  - **文件夹层级 scope**：调制轨挂在文件夹轨上，作用于文件夹内全部普通轨的设备；
  - **画笔画开关 clip**：用户在调制轨上画 on/off 区段（start/end 秒）；
  - **预分析特征确定性**：特征来自 `audio_feature_cache`（键=文件 sha256+版本）；
  - **烘焙签名纳入**：状态含特征键，进时间线渲染缓存失效签名；
  - **C 同步增益**：调制值在 bake/渲染参数表侧消费（本模块只产出"调制值序列"
    纯数据，CE/Python 同源消费——不做运行时逐帧 C 调用）。

调制语义（确定性纯函数）：t 落在某 on-clip → 包络值（attack/release 斜坡 +
特征强度），否则 0。调制值 = 包络 × 特征强度（按 target 插值）× 全局 amount，
全 0..1 标量，消费方按通道语义映射。

R180 断点包络（范式）：clip 带非空 `points` 时包络项改用
`audio_mod_envelope.eval_points`（归一化 t 的断点折线/幂曲线，**替代**
attack/release 斜坡；波形项仍照常相乘）；无 points → 旧路径逐位不变。
"""
from __future__ import annotations

import copy
import math
from typing import Dict, List, Optional

from ..audio.audio_features import AudioFeatures
from .audio_mod_envelope import eval_points, normalize_points, points_key
from .audio_mod_spread import clamp_deg
from .audio_mod_processor_state import processor_fields as _processor_fields, processor_rule_key as _processor_rule_key

try:
    # R178 P2-7：WAVE_FUNCS 移到模块顶（延迟 import 是每帧纯浪费）。已核查
    # effect_engine 及其依赖链不 import 本模块（无环）；src/ui/__init__.py 为
    # 空，不拖 Qt。effect_engine 不可用时波形退化为恒定平台（WAVE_FUNCS={}）。
    from ..ui.effect_engine import WAVE_FUNCS
except Exception:   # pragma: no cover
    WAVE_FUNCS: Dict[str, object] = {}

STATE_VERSION = 1

#: clip 最短秒（与 `_normalize_clip` 过窄丢弃/audio_mod_track.MIN_CLIP_S 同一口径）
MIN_CLIP_S = 0.05

#: per-clip fade「未设置」哨兵（负值 = 继承全局 attack/release；NaN 哨兵同族语义，
#: 复用 clip_view.py:187-200「只提交被改的一侧」的思路：未显式设置 ≠ 0）
FADE_INHERIT = -1.0

#: state dict 字段（Project.audio_modulation_state 的 schema）
# { "version","audio_sha"（绑音频，换歌失效）,"scope_folder_id"（空=不生效）,
#   "target_ch_types","amount" 0..1,"attack"/"release" 秒, "feature",
#   "spread_deg"/"phase_from_deg"/"phase_to_deg"（R183 3.4b 多灯，0=旧行为）,
#   "clips":[{start,end,effect_id,targets,points[[t,v,(c)]] R180 断点包络}] }

def normalize_state(state: Optional[dict]) -> dict:
    """工程里读出的 state 归一化（缺失/非法字段全部给默认；永不抛）。

    R182 Draw 扩展：`sources`/`rules`（编译产物，schema 见 audio_mod_draw_compile）
    + `draw_graph`（画布编辑态快照，重开恢复，运行时不消费）。`scope_track_id`
    不在本 schema，由 widget 单独留存回带。R183 3.4b 相位三项缺省 0=旧行为。
    """
    s = dict(state) if isinstance(state, dict) else {}
    return _processor_fields(s, {
        "version": STATE_VERSION,
        "audio_sha": _text(s.get("audio_sha")),
        "scope_folder_id": _text(s.get("scope_folder_id")),
        "target_ch_types": _targets(s.get("target_ch_types")),
        "amount": _clamp01(s.get("amount", 1.0)),
        "attack": max(0.0, _num(s.get("attack"), 0.05)),
        "release": max(0.0, _num(s.get("release"), 0.2)),
        "feature": str(s.get("feature", "beat_strength") or "beat_strength"),
        "default_wave": _text(s.get("default_wave")),   # 新画 clip 默认波形
        # R183 3.4b 多灯相位展开（0..360 度；缺省 0 = 旧行为逐位不变）
        "spread_deg": clamp_deg(s.get("spread_deg")),
        "phase_from_deg": clamp_deg(s.get("phase_from_deg")),
        "phase_to_deg": clamp_deg(s.get("phase_to_deg")),
        "clips": _normalize_clips(s.get("clips")),
        "sources": _dict_rows(s.get("sources")),
        "rules": _dict_rows(s.get("rules")),
        "draw_graph": _copy_graph(s.get("draw_graph")),
    })


def _text(v) -> str:
    """宽容字符串（None → ""）。"""
    return str(v or "")


def _targets(raw) -> List[str]:
    """target_ch_types 归一化（非 str 项丢弃）。

    ★ R182 四轮审计修：裸字符串（`"pump"`）此前被逐字符拆成
      `[p,u,m,p]`（静默数据腐化）——非 list/tuple 一律给空。
    """
    if isinstance(raw, str):
        return []
    return [str(t) for t in (raw or []) if isinstance(t, str) and t]


def _dict_rows(raw) -> List[dict]:
    """dict 行表浅拷贝（sources/rules 用；非 dict 行丢弃）。

    ★ R182 四轮审计修：非可迭代 raw（如 `rules: 7`）此前抛
      TypeError——违背 normalize_state「永不抛」契约，现给空表。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    return [dict(x) for x in raw if isinstance(x, dict)]


def _deep_node(n: dict) -> dict:
    """节点浅拷 + params/ch_types 深拷（清查⑦A P1-2：堵"未应用编辑经共享
    params dict 直落工程 state"的泄漏——与 compile_graph 深拷同口径）。"""
    d = dict(n)
    if isinstance(d.get("params"), dict):
        d["params"] = copy.deepcopy(d["params"])
    if isinstance(d.get("ch_types"), (list, tuple)):
        d["ch_types"] = list(d["ch_types"])
    return d


def _copy_graph(raw) -> dict:
    """draw_graph 快照（清查⑤①F3：非 dict → 空图幂等）。"""
    if not isinstance(raw, dict):
        return {"nodes": [], "edges": []}
    node = raw.get("nodes")
    edge = raw.get("edges")
    return {
        "nodes": [_deep_node(n) for n in node if isinstance(n, dict)] if isinstance(node, list) else [],
        "edges": [dict(e) for e in edge if isinstance(e, dict)] if isinstance(edge, list) else [],
    }


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v, default: int) -> int:
    """宽容 int（坏数据回退 default；R181 scope 识别回退链用）。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _clamp01(v) -> float:
    try:
        return min(1.0, max(0.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _freq_or_default(v, default: float = 2.0) -> float:
    """wave_freq = 区段内周期数（≥0 任意；非法/缺失给默认 2）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f >= 0.0 else default


def _normalize_clips(raw) -> List[Dict]:
    """clip 归一化：start/end 数值化+排序+过窄丢弃；effect_id/wave/targets 随带。"""
    clips: List[Dict] = []
    if not isinstance(raw, list):
        return clips
    for c in raw:
        clip = _normalize_clip(c)
        if clip is not None:
            clips.append(clip)
    clips.sort(key=lambda c: (c["start"], c["end"]))
    return clips


def _normalize_clip(c) -> Optional[Dict]:
    """单条 clip 归一化（非法/过窄/非有限值给 None=丢弃）。

    ★ 清查⑦A P1-1/P3-18：NaN/Inf 此前存活 → 幽灵区段（不可拖不可删不出声、
      签名永不稳、JSON 落盘非法）。照 `audio_mod_envelope._normalize_point`
      的 `t != t` 先例补有限性防线；负 start 钳 0。
    """
    if not isinstance(c, dict):
        return None
    try:
        start, end = float(c.get("start", 0.0)), float(c.get("end", 0.0))
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(start) and math.isfinite(end)):
        return None
    start = max(0.0, start)
    if end - start < MIN_CLIP_S:
        return None
    targets = _targets(c.get("targets"))   # 清查⑦A P3-15：裸串逐字符拆同口径防
    return {
        "start": round(start, 4),
        "end": round(end, 4),
        "effect_id": str(c.get("effect_id", "") or ""),
        "wave": str(c.get("wave", "") or ""),
        "wave_freq": _freq_or_default(c.get("wave_freq")),
        "targets": targets,
        "points": normalize_points(c.get("points")),   # R180：无/非法 → []
        # M1：per-clip 淡入/淡出（秒；哨兵 FADE_INHERIT = 继承全局）
        "fade_in": _clip_fade(c.get("fade_in")),
        "fade_out": _clip_fade(c.get("fade_out")),
    }


def _clip_fade(v) -> float:
    """per-clip fade 归一化：缺失/负/非法 → FADE_INHERIT（继承全局斜坡）。"""
    f = _num(v, FADE_INHERIT)
    return FADE_INHERIT if f < 0.0 else round(f, 4)


def _effective_fade(v, fallback: float) -> float:
    """生效 fade：哨兵（负）→ 全局值，否则 per-clip 值（`_clip_envelope_term` 用）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return fallback
    return fallback if f < 0.0 else max(0.0, f)


# ── M1 拖动/缩放纯函数（UI 写口的模型侧；重叠=排后者胜）────────────────────

def clip_index(clips: List[Dict], index_or_span) -> int:
    """定位 clip：int 下标或 (start,end) 元组键（与 delete_clip_range 同式）→ 下标。

    元组键按 normalize 的 round(,4) 口径比对；未命中 → -1。
    """
    if isinstance(index_or_span, bool):
        return -1
    if isinstance(index_or_span, (int, float)):
        i = int(index_or_span)
        return i if 0 <= i < len(clips) else -1
    if isinstance(index_or_span, (tuple, list)) and len(index_or_span) == 2:
        try:
            k = (round(float(index_or_span[0]), 4), round(float(index_or_span[1]), 4))
        except (TypeError, ValueError):
            return -1
        # ★ 清查③①F（P2-P3）：同 span 多条命中取**末条**（与渲染同层）。
        hit = -1
        for i, c in enumerate(clips):
            if (c["start"], c["end"]) == k:
                hit = i
        return hit
    return -1


def _resolve_overlaps(clips: List[Dict]) -> List[Dict]:
    """重叠归位：排后者胜，前者截短到后者 start（过窄丢弃）。
    ★ 清查⑤①F1：截短保形重参数化。"""
    clips.sort(key=lambda c: (c["start"], c["end"]))
    out: List[Dict] = []
    for c in clips:
        if out and c["start"] < out[-1]["end"] - 1e-9:
            new_end = round(float(c["start"]), 4)
            span = out[-1]["end"] - out[-1]["start"]
            if span > 1e-9 and isinstance(out[-1].get("points"), list)                         and len(out[-1]["points"]) >= 2:
                from .audio_mod_envelope import truncate_points_tail as _tpt
                kept = _tpt(out[-1]["points"], (new_end - out[-1]["start"]) / span)
                if kept is not None:
                    out[-1]["points"] = kept
                else:
                    out[-1].pop("points", None)
            out[-1]["end"] = new_end
            if out[-1]["end"] - out[-1]["start"] < MIN_CLIP_S:
                out.pop()
        out.append(c)
    return out


def move_clip(state: dict, index_or_span, new_start: float,
              audio_duration: float = 0.0) -> dict:
    """拖动 clip 平移（纯函数，返回新 state；入参不改）：保 span 平移，clamp ≥0
    且 ≤音频时长（调用方传，0=不钳上界）；落点重叠走 `_resolve_overlaps`。"""
    s = normalize_state(state)
    clips = [dict(c) for c in s["clips"]]
    i = clip_index(clips, index_or_span)
    if i < 0:
        return s
    span = clips[i]["end"] - clips[i]["start"]
    start = max(0.0, _num(new_start, clips[i]["start"]))
    if audio_duration > 0:
        start = min(start, max(0.0, float(audio_duration) - span))
    clips[i]["start"] = round(start, 4)
    clips[i]["end"] = round(start + span, 4)
    s["clips"] = _resolve_overlaps(clips)
    return s


def resize_clip(state: dict, index_or_span, new_end: float, edge: str = "end",
                audio_duration: float = 0.0) -> dict:
    """拖 clip 一边缩放（纯函数，返回新 state）：`new_end` = 被拖边缘的新秒值，
    `edge` ∈ {"end"|"r"} 右缘 / {"start"|"l"} 左缘；最小宽 MIN_CLIP_S、右缘钳
    音频时长（调用方传）；重叠归位同 `move_clip`。"""
    s = normalize_state(state)
    clips = [dict(c) for c in s["clips"]]
    i = clip_index(clips, index_or_span)
    if i < 0:
        return s
    c = clips[i]
    v = max(0.0, _num(new_end, c["end"]))
    if str(edge or "end").lower() in ("start", "l", "left", "in"):
        c["start"] = round(min(c["end"] - MIN_CLIP_S, v), 4)
    else:
        hi = float(audio_duration) if audio_duration > 0 else None
        if hi is not None:
            v = min(v, max(c["start"] + MIN_CLIP_S, hi))
        c["end"] = round(max(c["start"] + MIN_CLIP_S, v), 4)
    s["clips"] = _resolve_overlaps(clips)
    return s


def active_envelope(state: dict, t: float) -> float:
    """t 时刻的 clip 包络 0..1：在 clip 内 = attack/release 斜坡 × 波形（否则 0）。

    波形（用户规格 2026-09-29：调制集合不同效果预设形状）：clip.wave ∈
    WAVE_FUNCS 键（Sine/Square/…，effect_engine 同源），区段内按 wave_freq
    周期起伏；空 = 恒定平台（原行为）。
    """
    return _active_envelope_norm(normalize_state(state), t)


def _active_envelope_norm(s: dict, t: float) -> float:
    """`active_envelope` 正文（归一化 state；每帧快路径复用）。
    ★ 清查①B4（P1）：重叠区排后者胜（与拖动归位归一）。"""
    out = 0.0
    for c in s["clips"]:
        if c["start"] <= t <= c["end"]:
            ramp = _clip_envelope_term(s, c, t)
            out = _clamp01(ramp * _clip_wave_value(c, t))
    return out


def _clip_envelope_term(s: dict, c: Dict, t: float) -> float:
    """clip 包络项 0..1：有断点包络走 eval_points（替代斜坡），否则旧斜坡。

    M1：per-clip `fade_in/fade_out`（秒）非哨兵时**覆盖**全局 attack/release；
    哨兵（FADE_INHERIT）→ 逐位回旧全局斜坡行为（旧档零回归）。
    """
    pts = c.get("points") or []
    if pts:
        span = max(c["end"] - c["start"], 1e-6)
        return eval_points(pts, (t - c["start"]) / span)
    att = _effective_fade(c.get("fade_in"), s["attack"])
    rel = _effective_fade(c.get("fade_out"), s["release"])
    if t < c["start"] + att:
        span = max(att, 1e-6)
        return min(1.0, (t - c["start"]) / span)
    if t > c["end"] - rel:
        span = max(rel, 1e-6)
        return min(1.0, (c["end"] - t) / span)
    return 1.0


def _clip_wave_value(clip: Dict, t: float, phase_offset: float = 0.0) -> float:
    """clip 的波形因子 0..1（phase ∈ [0,1) = 区段内相对位置 × 频率 + 相位偏移）。

    R183 3.4b：`phase_offset`（0..1 周期）由多灯展开给出；缺省 0 = 旧口径逐位不变。
    """
    wave = str(clip.get("wave", "") or "")
    if not wave:
        return 1.0
    fn = WAVE_FUNCS.get(wave)   # 顶部 import（P2-7）；缺失键 → 恒定平台
    if fn is None:
        return 1.0
    span = max(float(clip["end"]) - float(clip["start"]), 1e-6)
    freq = max(0.0, float(clip.get("wave_freq", 2.0) or 0.0))
    phase = ((t - float(clip["start"])) / span) * freq + _num(phase_offset, 0.0)
    try:
        v = float(fn(phase))
    except Exception:
        return 1.0
    return min(1.0, max(0.0, v))


def feature_value(features: AudioFeatures, kind: str, t: float) -> float:
    """t 时刻的特征强度 0..1（拍网格最近拍采样；无特征给常数 1=纯包络调制）。"""
    if features is None or not features.beat_times:
        return 1.0
    i = _nearest_beat_index(features.beat_times, t)
    series = _feature_series(features, kind)
    return float(series[i]) if 0 <= i < len(series) else 1.0


def _nearest_beat_index(beat_times: List[float], t: float) -> int:
    """二分找最近拍的索引。"""
    lo, hi = 0, len(beat_times) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if beat_times[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    if lo > 0 and abs(beat_times[lo - 1] - t) < abs(beat_times[lo] - t):
        lo -= 1
    return lo


def _feature_series(features: AudioFeatures, kind: str) -> List[float]:
    """特征名 → 逐拍序列（未知名给常数序列=纯包络调制）。"""
    if kind == "beat_strength":
        return features.beat_strength
    if kind in ("band_low", "band_mid", "band_high"):
        idx = {"band_low": 0, "band_mid": 1, "band_high": 2}[kind]
        return [row[idx] if idx < len(row) else 0.0 for row in features.band_energy]
    if kind == "onset_flux":
        return features.onset_flux
    return [1.0] * len(features.beat_times)


def modulation_value(state: dict, features: Optional[AudioFeatures], t: float,
                     state_is_normalized: bool = False) -> float:
    """t 时刻的最终调制值 0..1 = 包络 × 特征 × amount（确定性，CE/Python 同源）。

    `state_is_normalized=True`：调用方保证 state 已是 normalize_state 产物
    （audio_mod_driver 的身份缓存），走零归一化快路径（R178 P2-7：此前每帧
    normalize+排序两次，纯浪费）。
    """
    if not state_is_normalized:
        state = normalize_state(state)
    env = _active_envelope_norm(state, t)
    if env <= 0.0:
        return 0.0
    feat = feature_value(features, state["feature"], t)
    return _clamp01(env * feat * state["amount"])


def modulated_positions(state: dict, features: Optional[AudioFeatures],
                        positions: List[tuple], t: float) -> List[float]:
    """设备位置表 [(x, y)...] → 每台的调制值。

    R183 3.4b 落地（此前「全设备同值」的预留入口）：按**列表序**（调用方按配接
    顺序传入）取每台相位偏移的调制值——`audio_mod_fan.clip_values` 与驱动侧消费
    同一实现；展开三项全 0 / 无位置 → 逐位回旧输出（同值列表）。**坐标（空间）
    展开**仍后置（Avolites Direction / MA3 2D 布局语义，见 spread-adoption §四）。
    """
    s = normalize_state(state)
    from .audio_mod_fan import clip_values
    vals = clip_values(s, features, t, len(positions or []),
                       state_is_normalized=True)
    if vals is None:
        v = modulation_value(s, features, t, state_is_normalized=True)
        return [v] * len(positions)
    return vals


def rules_key(state: dict) -> tuple:
    """Draw 规则/源卡内容的稳定指纹（进 `signature` → 编辑即解锁重烘）。

    R182：改波形/深度/接线/源卡 feature/效果参数任一 → 指纹变化。
    R182 重构（音频效果库）：指纹含效果 key、完整 params 表与多目标列表；
    旧字段（wave/amount/ch_type）仍参与以承旧档。`draw_graph` 的节点坐标
    **不进指纹**（挪卡不改变听感，不该触发重烘）。
    """
    s = state if isinstance(state, dict) else {}
    srcs = tuple((str(x.get("id", "")), str(x.get("feature", "")),
                  round(_num(x.get("phase"), 0.0), 4))
                 for x in (s.get("sources") or []) if isinstance(x, dict))
    # ★ 清查③①A'（P4）：sorted（对齐 F6 集合语义，异序不触发重播种）。
    rules = tuple((str(r.get("ch_type", "")),
                   tuple(sorted(str(c) for c in (r.get("ch_types") or []))),
                   str(r.get("effect", "")),
                   _params_key(r.get("params")),
                   str(r.get("wave", "")),
                   round(_num(r.get("wave_freq"), 2.0), 4),
                   round(_clamp01(r.get("amount", 1.0)), 4),
                   tuple(sorted(str(x) for x in (r.get("source_ids") or []))))
                  for r in (s.get("rules") or []) if isinstance(r, dict))
    return _processor_rule_key(s, (srcs, rules))


def _params_key(params) -> tuple:
    """效果参数表 → 稳定指纹元组（数值 4 位量化；非 dict → 空）。"""
    if not isinstance(params, dict):
        return ()
    out = []
    for k in sorted(params.keys()):
        v = params[k]
        out.append((str(k), v if isinstance(v, str)
                    else round(_num(v, 0.0), 4)))
    return tuple(out)


def signature(state: dict, feature_key: str) -> tuple:
    """烘焙失效签名：状态/特征键任一变化 → 整表失效重烘（含 per-clip fade、
    3.4b spread 三项、清查①B1 per-clip 波形四键，与 rules_key 同口径）。"""
    s = normalize_state(state)
    return (s["audio_sha"], s["scope_folder_id"], tuple(s["target_ch_types"]),
            s["amount"], s["attack"], s["release"], s["feature"],
            s["spread_deg"], s["phase_from_deg"], s["phase_to_deg"],
            tuple((c["start"], c["end"], c["fade_in"], c["fade_out"],
                   points_key(c.get("points")),
                   str(c.get("wave", "") or ""),
                   round(_num(c.get("wave_freq", 0.0), 0.0), 4),
                   str(c.get("effect_id", "") or ""),
                   tuple(sorted(str(t) for t in (c.get("targets") or []))))
                  for c in s["clips"]), rules_key(s), feature_key)
