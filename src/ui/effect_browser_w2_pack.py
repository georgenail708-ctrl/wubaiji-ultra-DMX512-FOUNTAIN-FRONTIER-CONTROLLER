# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_pack.py — R183 W2-FX #9：预设包导入 / 导出（.json）。

团队共享库的最小可用形态：把「我的效果」导出成一个人类可读的 JSON 文件，
别人导入后合并进自己的用户预设（同 id 跳过、同名自动加后缀 —— **绝不覆盖**）。

格式（``w2_store.pack_payload`` 生成，纯函数、可单测）::

    {"kind": "qascade.effect_w2_state.preset_pack", "version": 1,
     "exported": <unix ts>, "presets": [{"name","params","id","category",
                                         "description","tags"}]}

写入细节：同目录 ``.tmp`` + ``os.replace`` 原子写（照抄 ``effect_user_store``
的落盘范式，不引第三方存储）；导入先 ``parse_pack`` 校验再 ``plan_merge`` 规划
目标名，两条纯函数都已定测。文件对话框走 ``w2_dialogs``（统一取消语义）。

参考: 本仓 ``main_window`` 的 JSON 导入导出先例（QFileDialog + json.dump）；
      预设子集序列化见 ``effect_engine.py:337 EffectPreset.to_dict``。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from . import effect_browser_w2_base as _base
from . import effect_browser_w2_dialogs as _dlg
from . import effect_browser_w2_store as _store

logger = logging.getLogger(__name__)

#: 导出默认文件名
DEFAULT_NAME = "effect_presets.json"


def _user_presets():
    """用户预设存储模块（A 批；缺失 → None）。"""
    try:
        from . import effect_user_store as user_store
        return user_store
    except Exception:
        logger.debug("effect_browser_w2_pack: user store unavailable", exc_info=True)
        return None


def _entry(name: str, rec: dict) -> dict:
    """档条目 → 包条目（参数快照原样带出）。"""
    return {"name": str(name), "params": dict(rec.get("params") or {}),
            "id": str(rec.get("id", "") or ""),
            "category": str(rec.get("base", "") or ""),
            "description": str(rec.get("description", "") or ""),
            "tags": ["用户预设"]}


def collect_entries(wanted: Optional[set] = None) -> List[dict]:
    """收集要导出的条目（``wanted=None`` → 全部用户预设）。"""
    user_store = _user_presets()
    if user_store is None:
        return []
    out: List[dict] = []
    for name in list(user_store.list_user_presets()):
        rec = user_store.get_user_preset(name) or {}
        pid = str(rec.get("id", "") or "")
        if wanted is not None and pid not in wanted:
            continue
        out.append(_entry(name, rec))
    return out


def write_pack(path: str, entries: Sequence) -> int:
    """把条目写成预设包（原子写）；返回写出条数。"""
    doc = _store.pack_payload(entries)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(doc, fp, ensure_ascii=False, indent=1)
    os.replace(tmp, str(path))
    return len(doc.get("presets") or [])


def export_preset_pack(panel, path: str, preset_ids=None) -> int:
    """导出到 ``path``；空库时回退"导出当前选中预设的参数快照"。"""
    wanted = None if preset_ids is None else {str(x) for x in preset_ids}
    entries = collect_entries(wanted)
    if not entries:
        entries = _selected_entry(panel)
    if not entries:
        return 0
    return write_pack(path, entries)


def _selected_entry(panel) -> List[dict]:
    """当前选中预设的参数快照（无选中 → 空表）。"""
    preset_id = _base.preset_id_of(panel)
    if not preset_id:
        return []
    try:
        params = dict(panel._capture_current_param_values() or {})
    except Exception:
        logger.debug("effect_browser_w2_pack: capture params failed", exc_info=True)
        return []
    return [_entry(_base.display_name(panel, preset_id), {"id": preset_id,
                                                          "params": params})]


def export_dialog(panel) -> int:
    """弹「另存为」→ 导出；返回条数（取消 → -1）。"""
    path = _dlg.ask_save_path(panel, "导出效果预设包", DEFAULT_NAME)
    if not path:
        return -1
    count = export_preset_pack(panel, path)
    _base.set_status(panel, f"已导出 {count} 个预设")
    return count


def import_dialog(panel) -> Dict[str, Any]:
    """弹「打开」→ 导入；返回统计（取消 → {"added": -1}）。"""
    path = _dlg.ask_open_path(panel, "导入效果预设包")
    if not path:
        return {"added": -1, "skipped": 0, "errors": []}
    stats = import_preset_pack(panel, path)
    _base.set_status(panel, f"导入 {stats.get('added', 0)} 个，"
                            f"跳过 {stats.get('skipped', 0)} 个")
    return stats


def import_preset_pack(panel, path: str) -> Dict[str, Any]:
    """导入并合并（同 id 跳过、同名加后缀）；返回 ``{added, skipped, errors}``。"""
    user_store = _user_presets()
    if user_store is None:
        return {"added": 0, "skipped": 0, "errors": ["用户预设存储不可用"]}
    entries, errors = _read_entries(path)
    if not entries:
        return {"added": 0, "skipped": 0, "errors": errors or ["没有可导入条目"]}
    merger = _Merger(panel, user_store)
    for entry, target in _store.plan_merge(entries, merger.names, merger.ids):
        merger.add(entry, target)
    if merger.added:
        user_store.register_user_presets(_base.engine(), force=True)
        _base.refresh_list(panel)
    return {"added": merger.added, "skipped": merger.skipped, "errors": errors}


def _read_entries(path: str) -> tuple:
    """读文件并解析 → ``(条目表, 错误表)``（读/解析失败 → 单条错误）。"""
    try:
        with open(path, "r", encoding="utf-8") as fp:
            raw = json.load(fp)
    except Exception:
        logger.debug("effect_browser_w2_pack: read pack failed", exc_info=True)
        return [], ["文件读取失败"]
    return _store.parse_pack(raw)


class _Merger:
    """导入累加器：维护已占用的名字/id 表与统计（合并过程无分支嵌套）。

    ``pairs`` = 已存在的 ``(包内条目名, 来源预设 id)`` 二元组 —— 同一份包**再导入
    一次不会重复**：``save_user_preset`` 每次为新名字生成新 id，单看 id 表认不出
    "这条我已经导入过了"，故按 (条目名, 来源) 去重。
    """

    def __init__(self, panel, user_store):
        self.added = 0
        self.skipped = 0
        self.names = list(user_store.list_user_presets())
        self.ids = [str((user_store.get_user_preset(n) or {}).get("id", "") or "")
                    for n in self.names]
        self.pairs = {(str(n), str((user_store.get_user_preset(n) or {}).get("base", "")))
                      for n in self.names}
        self._panel = panel
        self._store_mod = user_store

    def add(self, entry: dict, target: str) -> None:
        """存一条（重复条目/目标名缺失/写盘失败 → 计入 skipped）。"""
        base = str(entry.get("id") or "") or _base.preset_id_of(self._panel)
        name = str(entry.get("name", "") or "").strip()
        if not target or not base or (name, base) in self.pairs:
            self.skipped += 1
            return
        pid = self._store_mod.save_user_preset(
            target, base, entry.get("params") or {},
            description=str(entry.get("description", "")))
        if not pid:
            self.skipped += 1
            return
        self.added += 1
        self.pairs.add((name, base))
        self.names.append(target)
        self.ids.append(str(pid))


__all__ = ["DEFAULT_NAME", "collect_entries", "export_dialog",
           "export_preset_pack", "import_dialog", "import_preset_pack",
           "write_pack"]
