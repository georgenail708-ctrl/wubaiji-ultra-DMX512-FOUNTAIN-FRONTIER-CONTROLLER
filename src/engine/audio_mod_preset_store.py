# -*- coding: utf-8 -*-
"""Draw 用户预设库（R183 M4）—— 纯逻辑无 Qt（engine 层，不反向 import UI）。

用途：用户在 Draw 画布的组合（sources + rules + draw_graph 编译产物 = 调制
状态）可存为**命名预设**，跨工程复用；支持导出/导入 JSON 交换文件。

存储位置（用户目录惯例，全仓无 QStandardPaths 先例故按既有惯例自定）：
    Windows  %APPDATA%/Qascade/audio_mod_presets.json
    其它     ~/.qascade/audio_mod_presets.json
环境变量 `QASCADE_AUDIO_MOD_PRESETS_DIR` 覆盖目录（测试隔离/便携版）。
写盘为原子写（tmp + os.replace），半写坏档不会污染既有库。

文件 schema：
    {"app": "Qascade", "kind": "qascade.audio_mod_presets", "version": 1,
     "license": "CC0-1.0",
     "presets": {"<名>": {"created": <ts>, "updated": <ts>, "state": {...}}}}

导入门槛（任务规格）：文件必须携带 version + license 字段，否则整体拒绝；
JSON 损坏 / 单条损坏 → 报告跳过，不抛异常（静默容错红线）。
往返契约：save→list→load 状态等价；export→import round-trip；返回值全部
深拷贝（JSON 往返），调用方改动不污染库或文件。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

#: 交换文件标识（kind + version 双字段；导入时校验）
SCHEMA_KIND = "qascade.audio_mod_presets"
SCHEMA_VERSION = 1
#: 默认许可（本库预设为应用内数据，采用数据常见宽松许可声明）
DEFAULT_LICENSE = "CC0-1.0"
#: 单条预设 state 序列化上限（防病态画布图撑爆文件）
MAX_STATE_BYTES = 2 * 1024 * 1024

_FILENAME = "audio_mod_presets.json"
_ENV_DIR = "QASCADE_AUDIO_MOD_PRESETS_DIR"


# ── 路径 ────────────────────────────────────────────────────────────────
def presets_dir() -> Path:
    """预设库目录（env 覆盖 → %APPDATA%/Qascade → ~/.qascade）。"""
    env = str(os.environ.get(_ENV_DIR, "") or "").strip()
    if env:
        return Path(env)
    appdata = str(os.environ.get("APPDATA", "") or "").strip()
    if appdata:
        return Path(appdata) / "Qascade"
    return Path.home() / ".qascade"


def presets_path() -> Path:
    """预设库文件完整路径（每次现算——测试 monkeypatch 目录即生效）。"""
    return presets_dir() / _FILENAME


# ── 文档读写 ────────────────────────────────────────────────────────────
def _empty_doc() -> dict:
    return {"app": "Qascade", "kind": SCHEMA_KIND, "version": SCHEMA_VERSION,
            "license": DEFAULT_LICENSE, "presets": {}}


def _read_doc() -> dict:
    """读库文件；缺失/损坏 → 空文档（静默容错，绝不抛）。"""
    try:
        raw = json.loads(Path(presets_path()).read_text(encoding="utf-8"))
    except Exception:
        return _empty_doc()
    if not isinstance(raw, dict):
        return _empty_doc()
    presets = raw.get("presets")
    if not isinstance(presets, dict):
        presets = {}
    return {"app": "Qascade", "kind": SCHEMA_KIND, "version": SCHEMA_VERSION,
            "license": str(raw.get("license", "") or DEFAULT_LICENSE),
            "presets": {str(k): v for k, v in presets.items()
                        if isinstance(v, dict)}}


def _write_doc(doc: dict) -> bool:
    """原子写库文件（tmp + os.replace）；任何 IO 异常 → False 不炸。"""
    try:
        path = Path(presets_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except Exception:
        return False


def _clean_name(name) -> str:
    """预设名规范化（strip；空/非文本 → ""=非法）。"""
    return str(name or "").strip()


def _ts(v, default: float) -> float:
    """时间戳容错（非数值 → default）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _copy_state(state) -> Optional[dict]:
    """state 深拷贝（JSON 往返）；非法/超限/不可序列化 → None。"""
    if not isinstance(state, dict):
        return None
    try:
        blob = json.dumps(state, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if len(blob.encode("utf-8")) > MAX_STATE_BYTES:
        return None
    try:
        return json.loads(blob)
    except (TypeError, ValueError):
        return None


# ── 公开 API（任务规格五入口）──────────────────────────────────────────
def list_presets() -> List[str]:
    """全部预设名（按名排序；库缺失/损坏 → 空表）。"""
    try:
        return sorted(str(k) for k in _read_doc()["presets"])
    except Exception:
        return []


def save_preset(name, state) -> bool:
    """存/覆盖命名预设；名非法或 state 非法 → False（不写盘）。"""
    nm = _clean_name(name)
    copied = _copy_state(state)
    if not nm or copied is None:
        return False
    doc = _read_doc()
    now = float(time.time())
    old = doc["presets"].get(nm)
    created = _ts(old.get("created"), now) if isinstance(old, dict) else now
    doc["presets"][nm] = {"created": created, "updated": now, "state": copied}
    return _write_doc(doc)


def load_preset(name) -> Optional[dict]:
    """取命名预设的 state（深拷贝）；不存在/损坏 → None。"""
    nm = _clean_name(name)
    if not nm:
        return None
    entry = _read_doc()["presets"].get(nm)
    if not isinstance(entry, dict):
        return None
    return _copy_state(entry.get("state"))


def delete_preset(name) -> bool:
    """删命名预设（R183 成熟化轮①）；不存在 → False（不写盘）。"""
    nm = _clean_name(name)
    if not nm:
        return False
    doc = _read_doc()
    if nm not in doc["presets"]:
        return False
    del doc["presets"][nm]
    return _write_doc(doc)


def rename_preset(old, new) -> bool:
    """重命名（保留 created 时间戳、刷新 updated）；旧缺或新名占用 → False。"""
    o = _clean_name(old)
    n = _clean_name(new)
    if not o or not n or o == n:
        return False
    doc = _read_doc()
    entry = doc["presets"].get(o)
    if not isinstance(entry, dict) or n in doc["presets"]:
        return False
    doc["presets"][n] = {"created": _ts(entry.get("created"), time.time()),
                         "updated": float(time.time()),
                         "state": _copy_state(entry.get("state"))}
    del doc["presets"][o]
    return _write_doc(doc)


def export_json(path, names=None) -> dict:
    """导出预设到交换 JSON（`names=None` = 全量）。

    返回报告 dict：{"ok", "count", "path", "skipped", "error"}——同名缺失
    记 skipped；IO 失败 error 备注，不抛异常。
    """
    doc = _read_doc()
    wanted = sorted(doc["presets"]) if names is None else [
        _clean_name(n) for n in names]
    out: Dict[str, dict] = {}
    skipped: List[str] = []
    for nm in wanted:
        entry = doc["presets"].get(nm)
        state = _copy_state(entry.get("state")) if isinstance(entry, dict) else None
        if not nm or state is None:
            skipped.append(nm)
            continue
        out[nm] = {"created": entry.get("created"), "updated": entry.get("updated"),
                   "state": state}
    payload = {"app": "Qascade", "kind": SCHEMA_KIND, "version": SCHEMA_VERSION,
               "license": DEFAULT_LICENSE, "presets": out}
    try:
        p = Path(path)
        if p.parent and str(p.parent):
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "count": 0, "path": str(path),
                "skipped": skipped, "error": f"write failed: {exc}"}
    return {"ok": True, "count": len(out), "path": str(path),
            "skipped": skipped, "error": ""}


def import_json(path, merge: bool = True) -> dict:
    """从交换 JSON 导入预设。

    `merge=True` 合并（同名以导入文件为准）；`merge=False` 整体替换。
    文件缺失/损坏/缺 version 或 license 字段 → `ok=False` + error（不抛）。
    单条损坏（state 非法）→ 记 skipped 跳过，其余照常导入。
    """
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "imported": [], "skipped": [], "error": f"read failed: {exc}"}
    err = _schema_error(raw)
    if err:
        return {"ok": False, "imported": [], "skipped": [], "error": err}
    incoming = raw.get("presets")
    incoming = incoming if isinstance(incoming, dict) else {}
    doc = _read_doc() if merge else _empty_doc()
    imported: List[str] = []
    skipped: List[str] = []
    for nm, entry in incoming.items():
        nm = _clean_name(nm)
        state = (_copy_state(entry.get("state")) if isinstance(entry, dict) else None)
        if not nm or state is None:
            skipped.append(nm)
            continue
        now = float(time.time())
        doc["presets"][nm] = {"created": _ts(entry.get("created"), now),
                              "updated": now, "state": state}
        imported.append(nm)
    if not _write_doc(doc):
        return {"ok": False, "imported": [], "skipped": skipped,
                "error": "store write failed"}
    return {"ok": True, "imported": imported, "skipped": skipped, "error": ""}


def _schema_error(raw) -> str:
    """交换文件 schema 校验（返回 ""=通过；否则错误文案）。"""
    if not isinstance(raw, dict):
        return "not a JSON object"
    if not str(raw.get("license", "")).strip():
        return "missing license"
    ret = _version_error(raw.get("version"))
    if ret:
        return ret
    kind = str(raw.get("kind", ""))
    if kind and kind != SCHEMA_KIND:
        return f"unexpected kind {kind!r}"
    return ""


def _version_error(ver) -> str:
    """version 字段门槛（必须整数且在支持区间内）。"""
    if not isinstance(ver, int):
        return "missing or invalid version"
    if ver < 1 or ver > SCHEMA_VERSION:
        return f"unsupported version {ver}"
    return ""
