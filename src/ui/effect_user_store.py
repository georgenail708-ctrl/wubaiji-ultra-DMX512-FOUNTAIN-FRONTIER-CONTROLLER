# -*- coding: utf-8 -*-
"""src/ui/effect_user_store.py — R183 批 A 效果库用户态（自定义预设 + 收藏星标）。

纯数据层（不 import 任何 Qt）；UI 侧只做展示与入口接线。

两个用户态：
  1. **用户预设**（「自定义/我的效果」细分类）：把当前效果参数另存为命名条目，
     支持重命名/删除；注入引擎时 = 基础预设**克隆**（参数结构/计算函数同源，
     仅换 id/name/category）→ 场景按 id 召回、包络绑定、音频桥接零改动。
  2. **收藏星标**（「收藏」伪大类）：完整复用 ``TOP_RECENT`` 的伪大类机制
     （见 effect_categories.TOP_FAV / order_favorites），持久化跨会话。

存储（照抄 audio_mod_preset_store 的成熟范式，不复用其文件：
键空间/语义不同，同档混装会让"一个文件损坏带走两类数据"）：
    Windows  %APPDATA%/Qascade/effect_user_state.json
    其它     ~/.qascade/effect_user_state.json
环境变量 ``QASCADE_EFFECT_USER_DIR`` 覆盖目录（测试隔离/便携版）。
写盘为原子写（同目录 tmp + os.replace），半写坏档不污染既有库；损坏静默。

为什么不用 QSettings（Qt 官方方案）：Windows 默认落注册表（NativeFormat），
用户资产不便备份/迁移/跨机复制，且官方文档未承诺原子写（``isAtomicSyncRequired``
保证的是 INI 合并，不是崩溃安全）；本仓用户目录 JSON 惯例已在
``audio_mod_preset_store`` 落地并被 M4 轮验证，保持单一范式。
参考: https://doc.qt.io/qt-6/qsettings.html
"""
from __future__ import annotations

import dataclasses
import json
import os
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .effect_categories import USER_CATEGORY
from .effect_recall_fade import FADE_MAX
from .effect_operating_preferences import channel_key


#  R183 批 A：用户效果状态（自定义预设 + 收藏星标）—— 纯逻辑、无 Qt
#
#  为什么另立一档而不复用 `audio_mod_preset_store`：后者是 Draw 调制图 state 的
#  库（键空间/语义不同），同档混装会让"一个文件损坏带走两类数据"。此处照抄它的
#  **成熟范式**（用户目录 JSON + tmp&os.replace 原子写 + 损坏静默 + 深拷贝往返），
#  不引第三方存储（Qt 自带 QSettings 亦可，但 QSettings 无原子写保证、Windows 落
#  注册表不便备份/迁移；本档是"用户资产"必须可备份可移植）。
#  目录惯例与 audio_mod_preset_store 一致：%APPDATA%/Qascade（本仓用户目录惯例），
#  非 Windows 回退 ~/.qascade，env `QASCADE_EFFECT_USER_DIR` 覆盖（测试隔离）
#  —— 全仓无 QStandardPaths 先例，不为此单独引入第二个路径来源。
# ═════════════════════════════════════════════════════════════════════════════

#: 档名（与 audio_mod_presets.json 同目录、不同文件）
USER_STATE_FILENAME = "effect_user_state.json"
#: 目录覆盖 env（测试隔离 / 便携版）
USER_STATE_ENV_DIR = "QASCADE_EFFECT_USER_DIR"
#: 档 schema 标识
USER_SCHEMA_KIND = "qascade.effect_user_state"
USER_SCHEMA_VERSION = 1
#: 用户预设 id 前缀（与内置预设 id 空间隔离）
USER_PRESET_PREFIX = "userfx_"
#: 单条参数快照上限（防病态参数撑爆档）
MAX_USER_PRESET_BYTES = 512 * 1024
#: 召回过渡时长上限（秒；0=立即=旧行为）——与缓动数学同源（避免双定义漂移）
RECALL_FADE_MAX = FADE_MAX


def user_state_dir() -> Path:
    """用户效果状态目录（env 覆盖 → %APPDATA%/Qascade → ~/.qascade）。"""
    env = str(os.environ.get(USER_STATE_ENV_DIR, "") or "").strip()
    if env:
        return Path(env)
    appdata = str(os.environ.get("APPDATA", "") or "").strip()
    if appdata:
        return Path(appdata) / "Qascade"
    return Path.home() / ".qascade"


def user_state_path() -> Path:
    """档路径（每次现算 —— 测试 monkeypatch 目录即生效）。"""
    return user_state_dir() / USER_STATE_FILENAME


def _empty_user_doc() -> dict:
    return {"app": "Qascade", "kind": USER_SCHEMA_KIND,
            "version": USER_SCHEMA_VERSION, "license": "CC0-1.0",
            "presets": {}, "favorites": [], "recall_fade": 0.0}


def _as_float(v, default: float) -> float:
    """数值容错（非数值 → default）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _doc_key(path: Path) -> tuple:
    """档指纹（路径, mtime_ns, size）——未变则复用缓存，绘制热路径零 IO。"""
    try:
        st = path.stat()
        return (str(path), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (str(path), 0, 0)


#: 读缓存：{key: 指纹, doc: 档}（写盘后同步刷新，避免自读旧档）
_DOC_CACHE: Dict[str, Any] = {"key": None, "doc": None}


def _read_user_doc() -> dict:
    """读档；缺失/损坏/半写 → 空档（静默容错，绝不抛）。"""
    path = user_state_path()
    key = _doc_key(path)
    cached = _DOC_CACHE.get("doc")
    if _DOC_CACHE.get("key") == key and isinstance(cached, dict):
        return cached
    doc = _empty_user_doc()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raw = None
    if isinstance(raw, dict):
        presets = raw.get("presets")
        if isinstance(presets, dict):
            doc["presets"] = {str(k): v for k, v in presets.items()
                              if isinstance(v, dict)}
        favs = raw.get("favorites")
        if isinstance(favs, (list, tuple)):
            doc["favorites"] = [str(x) for x in favs if str(x)]
        doc["recall_fade"] = _as_float(raw.get("recall_fade"), 0.0)
        doc["channel_type_preference"] = channel_key(raw.get("channel_type_preference"))
    _DOC_CACHE["key"] = key
    _DOC_CACHE["doc"] = doc
    return doc


def _write_user_doc(doc: dict) -> bool:
    """原子写（同目录 tmp + os.replace）；任何 IO 异常 → False 不炸。"""
    try:
        path = user_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception:
        return False
    _DOC_CACHE["key"] = _doc_key(path)
    _DOC_CACHE["doc"] = doc
    return True


def _json_copy(obj, limit: int = MAX_USER_PRESET_BYTES):
    """深拷贝（JSON 往返）；不可序列化/超限 → None（调用方据此拒收）。"""
    if not isinstance(obj, dict):
        return None
    try:
        blob = json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if len(blob.encode("utf-8")) > limit:
        return None
    try:
        return json.loads(blob)
    except (TypeError, ValueError):
        return None


def _clean_name(name) -> str:
    """预设名规范化（strip；空/非文本 → ""=非法）。"""
    return str(name or "").strip()


def make_user_preset_id(name, taken: Sequence = ()) -> str:
    """名字 → 稳定 id（crc32 十六进制；与既有 id 冲突时续号）。"""
    base = USER_PRESET_PREFIX + format(zlib.crc32(str(name).encode("utf-8")), "08x")
    pid = base
    n = 2
    used = set(str(x) for x in (taken or ()))
    while pid in used:
        pid = f"{base}_{n}"
        n += 1
    return pid


# ── 用户预设 CRUD ───────────────────────────────────────────────────────
def list_user_presets() -> List[str]:
    """全部用户预设名（按名排序；档缺失/损坏 → 空表）。"""
    try:
        return sorted(str(k) for k in _read_user_doc()["presets"])
    except Exception:
        return []


def get_user_preset(name) -> Optional[dict]:
    """取条目（深拷贝）：{id, base, params, description, created, updated}。"""
    nm = _clean_name(name)
    if not nm:
        return None
    return _json_copy(_read_user_doc()["presets"].get(nm))


def user_preset_params(preset_id) -> Optional[dict]:
    """按预设 id 取参数快照（深拷贝）；非用户预设/缺失 → None。"""
    want = str(preset_id or "")
    if not want:
        return None
    for rec in _read_user_doc()["presets"].values():
        if str(rec.get("id", "")) == want:
            return _json_copy(rec.get("params"))
    return None


def find_user_preset_name(preset_id) -> str:
    """按预设 id 反查预设名；非用户预设 → ""（重命名/删除入口用）。"""
    want = str(preset_id or "")
    if not want:
        return ""
    for nm, rec in _read_user_doc()["presets"].items():
        if str(rec.get("id", "")) == want:
            return str(nm)
    return ""


def save_user_preset(name, base_id, params, description: str = "") -> Optional[str]:
    """存/覆盖命名用户预设；返回落库 id（名/基础/参数非法 → None，不写盘）。

    覆盖同名条目时**保留原 id 与 created**（旧工程/场景按 id 召回不失效）。
    """
    nm = _clean_name(name)
    base = str(base_id or "")
    copied = _json_copy(params)
    if not nm or not base or copied is None:
        return None
    doc = _read_user_doc()
    now = float(time.time())
    old = doc["presets"].get(nm)
    taken = [str(r.get("id", "")) for k, r in doc["presets"].items() if k != nm]
    if isinstance(old, dict) and str(old.get("id", "") or ""):
        pid = str(old["id"])
        created = _as_float(old.get("created"), now)
    else:
        pid = make_user_preset_id(nm, taken)
        created = now
    doc["presets"][nm] = {"id": pid, "base": base, "params": copied,
                          "description": str(description or ""),
                          "created": created, "updated": now}
    return pid if _write_user_doc(doc) else None


def delete_user_preset(name) -> bool:
    """删用户预设；不存在/名非法 → False（不写盘）。"""
    nm = _clean_name(name)
    if not nm:
        return False
    doc = _read_user_doc()
    if nm not in doc["presets"]:
        return False
    del doc["presets"][nm]
    return _write_user_doc(doc)


def rename_user_preset(old, new) -> bool:
    """重命名（保留 id/created，刷新 updated）；旧缺或新名占用 → False。"""
    o = _clean_name(old)
    n = _clean_name(new)
    if not o or not n:
        return False
    doc = _read_user_doc()
    entry = doc["presets"].get(o)
    if not isinstance(entry, dict) or (n != o and n in doc["presets"]):
        return False
    if n == o:
        return True
    doc["presets"][n] = dict(entry, updated=float(time.time()))
    del doc["presets"][o]
    return _write_user_doc(doc)


# ── 收藏星标 ────────────────────────────────────────────────────────────
def list_favorites() -> List[str]:
    """收藏 id 列表（最近收藏在前、去重；档缺失/损坏 → 空表）。"""
    out: List[str] = []
    seen = set()
    try:
        favs = _read_user_doc()["favorites"]
    except Exception:
        return []
    for pid in favs or []:
        pid = str(pid)
        if pid and pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def is_favorite(preset_id) -> bool:
    """该预设是否已收藏（绘制热路径调用；读档走 mtime 缓存）。"""
    return str(preset_id or "") in set(list_favorites())


def set_favorite(preset_id, on: bool = True) -> bool:
    """置/撤收藏（收藏时置顶）；返回是否真的改变了榜。"""
    pid = str(preset_id or "")
    if not pid:
        return False
    doc = _read_user_doc()
    before = [str(x) for x in doc["favorites"]]
    favs = [x for x in before if x != pid]
    if bool(on):
        favs.insert(0, pid)
    if favs == before:
        return False
    doc["favorites"] = favs
    return _write_user_doc(doc)


def toggle_favorite(preset_id) -> bool:
    """切换收藏；返回切换后是否**已收藏**（非法 id → False）。"""
    pid = str(preset_id or "")
    if not pid:
        return False
    want = not is_favorite(pid)
    set_favorite(pid, want)
    return want


# ── 召回过渡偏好（0=立即）──────────────────────────────────────────────
def recall_fade_pref() -> float:
    """用户设定的召回过渡秒数（夹到 [0, RECALL_FADE_MAX]；缺省 0.0=旧行为）。"""
    raw = _as_float(_read_user_doc().get("recall_fade"), 0.0)
    return max(0.0, min(RECALL_FADE_MAX, raw))


def set_recall_fade_pref(seconds) -> bool:
    """写召回过渡秒数偏好（夹到 [0, RECALL_FADE_MAX]）。"""
    doc = _read_user_doc()
    doc["recall_fade"] = max(0.0, min(RECALL_FADE_MAX,
                                      _as_float(seconds, 0.0)))
    return _write_user_doc(doc)


def channel_type_pref() -> str:
    """Last explicit channel type selected by the operator (stable ID)."""
    return channel_key(_read_user_doc().get("channel_type_preference"))


def set_channel_type_pref(value) -> bool:
    key = channel_key(value)
    if not key:
        return False
    doc = _read_user_doc()
    doc["channel_type_preference"] = key
    return _write_user_doc(doc)


# ── 注入效果引擎（用户预设 = 基础预设克隆 + 参数快照）──────────────────
#: 最近一次注册状态：{sig: 档指纹, engine: 引擎对象, ids: 已注册 id 元组}
_REGISTERED: Dict[str, Any] = {"sig": None, "engine": None, "ids": ()}


def _clone_user_preset(base, name, rec):
    """基础预设 + 档条目 → 注入用克隆（参数结构/计算函数同源，仅换元数据）。

    `params=list(base.params)` 是**浅拷贝的同批 ParamSpec 对象**：用户预设与
    基础预设参数结构必须逐项一致（否则 `_param_tree_signature` 复用判定与
    引擎参数读取会打架），仅列表容器独立 —— 避免 `_reg` 的通用参数追加
    把两个预设的 params 绑到同一个 list 上。
    """
    return dataclasses.replace(
        base, id=str(rec.get("id", "") or ""), name=str(name),
        category=USER_CATEGORY,
        description=str(rec.get("description", "") or base.description),
        params=list(base.params), tags=["用户预设", str(name)])


def _user_preset_entries(engine) -> List[tuple]:
    """档里**可注入**的 (名, 条目, 基础预设, 计算函数) 清单。

    基础预设已下架/缺失、或计算函数不在册 → 跳过（旧档静默降级，绝不抛）。
    """
    fns = getattr(engine, "_compute_fns", None)
    if not isinstance(fns, dict):
        return []
    out: List[tuple] = []
    for nm, rec in _read_user_doc()["presets"].items():
        pid = str(rec.get("id", "") or "")
        base_id = str(rec.get("base", "") or "")
        base = engine.get_preset(base_id)
        fn = fns.get(base_id)
        if pid and base is not None and fn is not None:
            out.append((str(nm), rec, base, fn))
    return out


def _drop_stale_user_presets(engine, keep: Sequence) -> None:
    """撤下本次未再注册的旧 id（条目被删/改名/基础缺失）——防止幽灵条目留在库里。"""
    store = getattr(engine, "_presets", None)
    fns = getattr(engine, "_compute_fns", None)
    if not isinstance(store, dict) or not isinstance(fns, dict):
        return
    for pid in _REGISTERED["ids"]:
        if pid not in set(str(x) for x in keep):
            store.pop(pid, None)
            fns.pop(pid, None)


def register_user_presets(engine=None, force: bool = False) -> int:
    """把用户预设注入效果引擎（幂等；返回成功注入条数）。

    注入形态 = 基础预设的**克隆**（参数结构/计算函数同源，仅换 id/name/分类），
    因此场景按 id 召回、包络绑定、音频桥接全部照常工作；基础预设已下架或缺失的
    条目跳过（不炸）。档未变（mtime+size 指纹相同）且引擎未换 → 零开销返回。
    """
    from .effect_engine import get_engine
    eng = engine if engine is not None else get_engine()
    sig = _doc_key(user_state_path())
    if not force and _REGISTERED["sig"] == sig and _REGISTERED["engine"] is eng:
        return 0
    entries = _user_preset_entries(eng)
    for nm, rec, base, fn in entries:
        eng._reg(_clone_user_preset(base, nm, rec), fn)
    _drop_stale_user_presets(eng, [rec.get("id") for _nm, rec, _b, _f in entries])
    _REGISTERED.update(sig=sig, engine=eng,
                       ids=tuple(str(rec.get("id", "") or "")
                                 for _nm, rec, _b, _f in entries))
    return len(entries)
