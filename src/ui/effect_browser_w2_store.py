# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_store.py — R183 W2-FX 效果浏览器用户态扩展（纯数据，无 Qt）。

深查② §一 剩余项的**数据层**（UI 侧只做展示与接线）：
  · #3 标签编辑器    → ``tags``：预设 id → 用户标签表（检索 hay 会拼进查询）
  · #10 使用计数/热度 → ``usage``：预设 id → 次数（「热度」伪大类排序键）
  · #8 参数宏        → ``macros``：预设 id → {宏键: {参数键: 系数}}
  · #13 跟随音频     → ``audio_follow``：预设 id → {参数键: {band, depth}}
  · 偏好            → ``prefs``：悬停试听开关 / 骰子微扰模式 / 缩略动画开关

存储（**不复用** A 批 ``effect_user_state.json``，也不复用其模块）：
    <QASCADE_EFFECT_USER_DIR>/effect_w2_state.json
    %APPDATA%/Qascade/effect_w2_state.json 或 ~/.qascade/effect_w2_state.json
目录 env 与 A 批**同名**（``QASCADE_EFFECT_USER_DIR``）—— 测试只需隔离一个目录；
档名不同 —— 一份档损坏不带走另一类数据（与 A 批"另立一档"的理由一致）。

原子写（同目录 tmp + ``os.replace``）+ mtime/size 指纹读缓存 + 损坏静默：
绘制热路径（卡片徽标、检索）不产生重复 IO；任何异常都不向调用方抛。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 档名（与 A 批 effect_user_state.json 同目录、不同文件）
W2_STATE_FILENAME = "effect_w2_state.json"
#: 目录覆盖 env（与 A 批同名：一处隔离即覆盖全部用户态）
W2_STATE_ENV_DIR = "QASCADE_EFFECT_USER_DIR"
#: 档 schema 标识
W2_SCHEMA_KIND = "qascade.effect_w2_state"
W2_SCHEMA_VERSION = 1
#: 标签约束（防病态档）
MAX_TAGS_PER_PRESET = 32
MAX_TAG_LEN = 24
#: 宏键（UI 三旋钮）
MACRO_KEYS: Tuple[str, ...] = ("m1", "m2", "m3")
#: 跟随音频可选频段（取 ``_audio_frame_dict`` 的键）
AUDIO_BANDS: Tuple[str, ...] = ("low", "mid", "high", "rms", "peak", "beat_phase")
#: 宏系数上限（映射参数归一化行程的倍数）
MACRO_SCALE_MAX = 4.0
#: 偏好默认值（未知键 → 默认；写未知键被忽略）
PREF_DEFAULTS: Dict[str, Any] = {
    "hover_preview": False,     # 悬停试听：默认关（现场误触发比少一个功能更糟）
    "dice_perturb": True,       # 「随机」= ±30% 微扰（#15；关掉=全域随机旧行为）
    "thumb_anim": True,         # 卡片缩略条动画（#14；关掉=零引擎计算）
    "heat_first": False,        # 热度优先排序（#10；默认关 = 旧排序逐字节不变）
}


def user_w2_dir() -> Path:
    """用户态目录（env 覆盖 → %APPDATA%/Qascade → ~/.qascade）。"""
    env = str(os.environ.get(W2_STATE_ENV_DIR, "") or "").strip()
    if env:
        return Path(env)
    appdata = str(os.environ.get("APPDATA", "") or "").strip()
    if appdata:
        return Path(appdata) / "Qascade"
    return Path.home() / ".qascade"


def user_w2_path() -> Path:
    """档路径（每次现算 —— 测试 monkeypatch 目录即生效）。"""
    return user_w2_dir() / W2_STATE_FILENAME


def _empty_doc() -> dict:
    return {"app": "Qascade", "kind": W2_SCHEMA_KIND, "version": W2_SCHEMA_VERSION,
            "tags": {}, "usage": {}, "macros": {}, "audio_follow": {}, "prefs": {}}


def _as_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _as_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def _doc_key(path: Path) -> tuple:
    """档指纹（路径, mtime_ns, size）；未变 → 复用缓存（热路径零 IO）。"""
    try:
        st = path.stat()
        return (str(path), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (str(path), 0, 0)


#: 读缓存：{key, doc, stat}
#:  ``stat`` = {"path","at","key"} —— `os.stat` 的**短窗缓存**：一次检索要对
#:  1200+ 预设问标签，若每次都 stat 就是每次按键多 ~80ms 的纯 IO。本档只由本
#:  进程经本模块写（写盘即刷新指纹），1 秒的 stat 窗口不会让"刚存的标签"看不见。
_DOC_CACHE: Dict[str, Any] = {"key": None, "doc": None, "stat": None}
#: stat 缓存有效期（秒）
_STAT_TTL_S = 1.0


def _stat_key(path: Path) -> tuple:
    """档指纹（带短窗缓存）：未变则复用上一次 ``stat`` 结果。"""
    now = time.monotonic()
    cached = _DOC_CACHE.get("stat")
    if (isinstance(cached, dict) and cached.get("path") == str(path)
            and now - float(cached.get("at", 0.0)) < _STAT_TTL_S):
        return cached["key"]
    key = _doc_key(path)
    _DOC_CACHE["stat"] = {"path": str(path), "at": now, "key": key}
    return key


def _clean_tags(tags) -> List[str]:
    """标签规范化：去空白/去重/限长限量（保序，绝不抛）。"""
    out: List[str] = []
    seen = set()
    for t in (tags or []):
        s = str(t or "").strip()[:MAX_TAG_LEN]
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= MAX_TAGS_PER_PRESET:
            break
    return out


def _int_map(raw, lo: int = 0) -> Dict[str, int]:
    """{str: int} 容错清洗（非 dict/非数值 → 丢弃该键）。"""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, int] = {}
    for k, v in raw.items():
        key = str(k or "")
        if key:
            out[key] = max(lo, _as_int(v, lo))
    return out


def _read_doc() -> dict:
    """读档；缺失/损坏/半写 → 空档（静默容错，绝不抛）。"""
    path = user_w2_path()
    key = _stat_key(path)
    cached = _DOC_CACHE.get("doc")
    if _DOC_CACHE.get("key") == key and isinstance(cached, dict):
        return cached
    doc = _empty_doc()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = None
    if isinstance(raw, dict):
        tags = raw.get("tags")
        if isinstance(tags, dict):
            doc["tags"] = {str(k): _clean_tags(v) for k, v in tags.items() if str(k)}
        doc["usage"] = _int_map(raw.get("usage"))
        doc["macros"] = _clean_macros(raw.get("macros"))
        doc["audio_follow"] = _clean_audio_follow(raw.get("audio_follow"))
        prefs = raw.get("prefs")
        if isinstance(prefs, dict):
            doc["prefs"] = {str(k): v for k, v in prefs.items()
                            if str(k) in PREF_DEFAULTS}
    _DOC_CACHE["key"] = key
    _DOC_CACHE["doc"] = doc
    _DOC_CACHE["stat"] = {"path": str(path), "at": time.monotonic(), "key": key}
    return doc


def _write_doc(doc: dict) -> bool:
    """原子写（同目录 tmp + os.replace）；任何 IO 异常 → False 不炸。"""
    try:
        path = user_w2_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception:
        return False
    # 写盘后立即刷新指纹与 stat 窗口（本周期的"刚存的标签"必须马上可见）
    key = _doc_key(path)
    _DOC_CACHE["key"] = key
    _DOC_CACHE["doc"] = doc
    _DOC_CACHE["stat"] = {"path": str(path), "at": time.monotonic(), "key": key}
    _TAGS_VIEW["key"] = None          # 写盘即失效派生视图（下一次读重建）
    _TAGS_VIEW["map"] = {}
    return True


# ── 标签（#3）────────────────────────────────────────────────────────────
#: 标签视图缓存（按档指纹键）：搜索过滤要在一次调用里问 1200+ 个预设的标签，
#: 逐个重读档会让每次按键多花 ~100ms（实测 1223 行触发 2060 次 stat）→ 缓存。
_TAGS_VIEW: Dict[str, Any] = {"key": None, "map": {}}


def _tags_view() -> Dict[str, List[str]]:
    """{预设 id: [标签]} 视图（档未变则复用；每次写盘后失效重建）。"""
    doc = _read_doc()
    key = _DOC_CACHE.get("key")
    cached = _TAGS_VIEW.get("map")
    if _TAGS_VIEW.get("key") == key and isinstance(cached, dict):
        return cached
    view = {str(k): list(v) for k, v in doc["tags"].items()}
    _TAGS_VIEW["key"] = key
    _TAGS_VIEW["map"] = view
    return view


def tags_map() -> Dict[str, List[str]]:
    """全量用户标签表 {预设 id: [标签]}（深拷贝）。"""
    try:
        return {k: list(v) for k, v in _tags_view().items()}
    except Exception:
        return {}


def user_tags(preset_id) -> List[str]:
    """该预设的用户标签（缺省 → 空表）。"""
    try:
        return list(_tags_view().get(str(preset_id or ""), []))
    except Exception:
        return []


def set_user_tags(preset_id, tags) -> bool:
    """覆盖写该预设的用户标签；空表 = 删键（不写空壳）。"""
    pid = str(preset_id or "")
    if not pid:
        return False
    cleaned = _clean_tags(tags)
    doc = _read_doc()
    if cleaned:
        doc["tags"][pid] = cleaned
    else:
        doc["tags"].pop(pid, None)
    return _write_doc(doc)


def toggle_user_tag(preset_id, tag) -> bool:
    """切换单个标签；返回切换后是否**已含**该标签。"""
    pid = str(preset_id or "")
    t = str(tag or "").strip()[:MAX_TAG_LEN]
    if not pid or not t:
        return False
    cur = user_tags(pid)
    if t in cur:
        cur = [x for x in cur if x != t]
        on = False
    else:
        cur.append(t)
        on = True
    set_user_tags(pid, cur)
    return on


# ── 使用计数 / 热度（#10）─────────────────────────────────────────────────
def usage_counts() -> Dict[str, int]:
    """全量使用计数 {预设 id: 次数}。"""
    try:
        return dict(_read_doc()["usage"])
    except Exception:
        return {}


def usage_count(preset_id) -> int:
    try:
        return int(usage_counts().get(str(preset_id or ""), 0))
    except Exception:
        return 0


def bump_usage(preset_id, by: int = 1) -> int:
    """累加使用次数；返回累加后的次数（非法 id → 0 不写盘）。"""
    pid = str(preset_id or "")
    if not pid:
        return 0
    doc = _read_doc()
    n = max(0, _as_int(doc["usage"].get(pid), 0)) + max(1, _as_int(by, 1))
    doc["usage"][pid] = n
    _write_doc(doc)
    return n


def heat_ids(limit: int = 0) -> List[str]:
    """按次数降序的预设 id 表（次数相同 → 保持插入序；limit>0 截断）。"""
    counts = usage_counts()
    ids = [k for k, v in counts.items() if v > 0]
    ids.sort(key=lambda k: -counts.get(k, 0))
    return ids[:int(limit)] if limit and int(limit) > 0 else ids


def heat_count() -> int:
    """有使用记录的预设数（分类条「热度」chip 的计数）。"""
    return len(heat_ids())


def order_by_heat(presets: Sequence, limit: int = 0) -> List:
    """按热度序返回子集（仅库中仍存在的 id；未用过的预设不进榜）。"""
    by_id = {str(getattr(p, "id", "")): p for p in presets}
    out: List = []
    for pid in heat_ids(limit):
        preset = by_id.get(pid)
        if preset is not None:
            out.append(preset)
    return out


def heat_rank_map() -> Dict[str, int]:
    """{预设 id: 热度名次}（名次越小越热；供视图稳定排序用）。"""
    return {pid: i for i, pid in enumerate(heat_ids())}


# ── 参数宏（#8）──────────────────────────────────────────────────────────
def _clean_macros(raw) -> Dict[str, Dict[str, Dict[str, float]]]:
    """macros 档清洗：{pid: {macro_key: {param_key: scale}}}（数值+键白名单）。"""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for pid, per in raw.items():
        slots = _macro_slots(per)
        if str(pid or "") and slots:
            out[str(pid)] = slots
    return out


def _macro_slots(per) -> Dict[str, Dict[str, float]]:
    """一个预设的宏槽表（非 dict / 槽键非法 / 空映射一律丢弃）。"""
    if not isinstance(per, dict):
        return {}
    slots: Dict[str, Dict[str, float]] = {}
    for mkey, mapping in per.items():
        m = str(mkey or "")
        if m in MACRO_KEYS and isinstance(mapping, dict):
            slots[m] = {str(pk): _as_float(sv, 1.0) for pk, sv in mapping.items()
                        if str(pk)}
    return slots


def macros_map() -> Dict[str, Dict[str, Dict[str, float]]]:
    """全量宏定义（深拷贝）。"""
    try:
        raw = _read_doc()["macros"]
        return {p: {m: dict(mm) for m, mm in per.items()} for p, per in raw.items()}
    except Exception:
        return {}


def macro_def(preset_id) -> Dict[str, Dict[str, float]]:
    """该预设的宏定义 {宏键: {参数键: 系数}}（缺省 → 空表）。"""
    return dict(macros_map().get(str(preset_id or ""), {}))


def set_macro(preset_id, macro_key, mapping) -> bool:
    """覆盖写一个宏槽；mapping 空 → 删该槽（预设全空 → 删预设键）。"""
    pid = str(preset_id or "")
    mkey = str(macro_key or "")
    if not pid or mkey not in MACRO_KEYS:
        return False
    clean = {str(k): max(-MACRO_SCALE_MAX, min(MACRO_SCALE_MAX, _as_float(v, 1.0)))
             for k, v in (mapping or {}).items() if str(k)}
    doc = _read_doc()
    per = doc["macros"].setdefault(pid, {})
    if clean:
        per[mkey] = clean
    else:
        per.pop(mkey, None)
    if not per:
        doc["macros"].pop(pid, None)
    return _write_doc(doc)


# ── 跟随音频（#13）───────────────────────────────────────────────────────
def _clean_audio_follow(raw) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """audio_follow 清洗：{pid: {param_key: {band, depth}}}。"""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for pid, per in raw.items():
        entries = _audio_entries(per)
        if str(pid or "") and entries:
            out[str(pid)] = entries
    return out


def _audio_entries(per) -> Dict[str, Dict[str, Any]]:
    """一个预设的绑定表（频段不在白名单 / 非 dict 条目一律丢弃）。"""
    if not isinstance(per, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for pk, rec in per.items():
        band = _band_of(rec)
        if band:
            out[str(pk)] = {"band": band, "depth": _depth_of(rec)}
    return out


def _band_of(rec) -> str:
    """条目里的频段（非白名单 → ""）。"""
    band = str(rec.get("band", "") if isinstance(rec, dict) else "")
    return band if band in AUDIO_BANDS else ""


def _depth_of(rec) -> float:
    """条目里的深度（夹到 [0,1]；缺失 → 0.5）。"""
    return max(0.0, min(1.0, _as_float((rec or {}).get("depth"), 0.5)))


def audio_follow_map() -> Dict[str, Dict[str, Dict[str, Any]]]:
    """全量跟随音频绑定（深拷贝）。"""
    try:
        raw = _read_doc()["audio_follow"]
        return {p: {k: dict(v) for k, v in per.items()} for p, per in raw.items()}
    except Exception:
        return {}


def audio_follow(preset_id) -> Dict[str, Dict[str, Any]]:
    """该预设的参数 → 音频绑定表（缺省 → 空表）。"""
    return dict(audio_follow_map().get(str(preset_id or ""), {}))


def set_audio_follow(preset_id, param_key, band=None, depth: float = 0.5) -> bool:
    """写/清一个跟随音频绑定（band 非法或 None → 清该参数）。"""
    pid = str(preset_id or "")
    pk = str(param_key or "")
    if not pid or not pk:
        return False
    b = str(band or "")
    doc = _read_doc()
    per = doc["audio_follow"].setdefault(pid, {})
    if b in AUDIO_BANDS:
        per[pk] = {"band": b,
                   "depth": max(0.0, min(1.0, _as_float(depth, 0.5)))}
    else:
        per.pop(pk, None)
    if not per:
        doc["audio_follow"].pop(pid, None)
    return _write_doc(doc)


# ── 偏好 ────────────────────────────────────────────────────────────────
def pref(name: str, default=None):
    """读偏好（未知键 → default；档缺失 → 该键默认）。"""
    key = str(name or "")
    if key not in PREF_DEFAULTS:
        return default
    fallback = PREF_DEFAULTS[key] if default is None else default
    try:
        val = _read_doc()["prefs"].get(key, fallback)
    except Exception:
        return fallback
    return bool(val) if isinstance(fallback, bool) else val


def set_pref(name: str, value) -> bool:
    """写偏好（未知键 → False 不写盘）。"""
    key = str(name or "")
    if key not in PREF_DEFAULTS:
        return False
    doc = _read_doc()
    doc["prefs"][key] = bool(value) if isinstance(PREF_DEFAULTS[key], bool) else value
    return _write_doc(doc)


# ── 预设包（#9）：编解码在 ``effect_browser_w2_codec``（纯函数），此处只再导出 ──
from .effect_browser_w2_codec import (  # noqa: E402  (放在功能块之后：保持阅读顺序)
    clean_tags as _clean_tags,
    pack_payload,
    parse_pack,
    plan_merge,
)

__all__ = [
    "AUDIO_BANDS", "MACRO_KEYS", "MACRO_SCALE_MAX", "MAX_TAGS_PER_PRESET",
    "MAX_TAG_LEN", "PREF_DEFAULTS", "W2_SCHEMA_KIND", "W2_STATE_ENV_DIR",
    "W2_STATE_FILENAME", "audio_follow", "audio_follow_map", "bump_usage",
    "heat_count", "heat_ids", "heat_rank_map", "is_fav", "macro_def",
    "macros_map", "order_by_heat", "pack_payload", "parse_pack", "plan_merge",
    "pref", "set_audio_follow", "set_macro", "set_pref", "set_user_tags",
    "tags_map", "toggle_user_tag", "usage_count", "usage_counts", "user_tags",
    "user_w2_dir", "user_w2_path",
]
