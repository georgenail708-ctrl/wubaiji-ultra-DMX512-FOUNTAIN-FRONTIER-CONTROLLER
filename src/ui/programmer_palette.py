# -*- coding: utf-8 -*-
"""programmer_palette — Programmer 命名调色板（Palette 预置位，R183 批次 I 项 8）。

参考语义：官方 Palettes 三页 / gMA3 Presets —— 把"一组通道值"
（ch_type → 值表）存成命名预置，之后对任意设备组一键复用。

与既有 `motif_system` / `xy_reference` 的区别：那两者是**引擎内部**的复用雏形
（按效果/同步组），本模块是**用户级、跨组、可命名**的调色板库，与效果库同构
（effect browser 的历史/收藏语义）。

存储（照 `src/engine/audio_mod_preset_store.py` 既有惯例，不新引依赖）：
    Windows  %APPDATA%/Qascade/programmer_palettes.json
    其它     ~/.qascade/programmer_palettes.json
环境变量 `QASCADE_PROG_PALETTES_DIR` 覆盖目录（测试隔离/便携版）。
原子写（tmp + os.replace）；损坏静默（空库，绝不抛）。

数据 schema：
    {"app": "Qascade", "kind": "qascade.programmer_palettes", "version": 1,
     "license": "CC0-1.0",
     "palettes": {"<名>": {"created": ts, "updated": ts,
                           "values": {"<ch_type>": 0..255},
                           "kinds": ["color","position"]}}}

★ 应用侧按 fixture 能力过滤：设备没有该 ch_type 就不写（复用 Programmer 既有
  `_fixture_supports_logical_channel` 口径，见 `values_for_fixture`）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SCHEMA_KIND = "qascade.programmer_palettes"
SCHEMA_VERSION = 1
DEFAULT_LICENSE = "CC0-1.0"

_FILENAME = "programmer_palettes.json"
_ENV_DIR = "QASCADE_PROG_PALETTES_DIR"

#: 调色板"类别"推断表（仅作 UI 分组提示，不参与写值）。
PALETTE_KINDS: Dict[str, Tuple[str, ...]] = {
    "color": ("R", "G", "B", "W", "A", "L", "UV"),
    "position": ("motor_x", "motor_y", "motor_z", "motor_x_fine",
                 "motor_y_fine", "motor_z_fine", "pan", "tilt"),
    "beam": ("dimmer", "zoom", "focus", "iris", "gobo", "prism", "strobe"),
}


def palettes_dir() -> Path:
    """调色板库目录（env 覆盖 → %APPDATA%/Qascade → ~/.qascade）。"""
    env = str(os.environ.get(_ENV_DIR, "") or "").strip()
    if env:
        return Path(env)
    appdata = str(os.environ.get("APPDATA", "") or "").strip()
    if appdata:
        return Path(appdata) / "Qascade"
    return Path.home() / ".qascade"


def palettes_path() -> Path:
    """库文件完整路径（每次现算——测试 monkeypatch 目录即生效）。"""
    return palettes_dir() / _FILENAME


def _empty_doc() -> dict:
    return {"app": "Qascade", "kind": SCHEMA_KIND, "version": SCHEMA_VERSION,
            "license": DEFAULT_LICENSE, "palettes": {}}


def _read_doc() -> dict:
    """读库文件；缺失/损坏 → 空文档（静默容错，绝不抛）。"""
    try:
        raw = json.loads(Path(palettes_path()).read_text(encoding="utf-8"))
    except Exception:
        return _empty_doc()
    if not isinstance(raw, dict):
        return _empty_doc()
    palettes = raw.get("palettes")
    if not isinstance(palettes, dict):
        palettes = {}
    return {"app": "Qascade", "kind": SCHEMA_KIND, "version": SCHEMA_VERSION,
            "license": str(raw.get("license", "") or DEFAULT_LICENSE),
            "palettes": {str(k): v for k, v in palettes.items()
                         if isinstance(v, dict)}}


def _write_doc(doc: dict) -> bool:
    """原子写（tmp + os.replace）；任何 IO 异常 → False 不炸。"""
    try:
        path = Path(palettes_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(str(tmp), str(path))
        return True
    except Exception:
        return False


def clean_values(values: Any, limit: int = 512) -> Dict[str, int]:
    """规整值表：`{ch_type: 0..255}`；坏项丢弃，超量截断（防病态文件）。"""
    out: Dict[str, int] = {}
    if not isinstance(values, dict):
        return out
    for ct, raw in values.items():
        sct = str(ct or "").strip()
        if not sct:
            continue
        try:
            val = int(round(float(raw)))
        except (TypeError, ValueError):
            continue
        out[sct] = max(0, min(255, val))
        if len(out) >= max(1, int(limit)):
            break
    return out


def classify_kinds(values: Dict[str, int]) -> List[str]:
    """按值表里出现的通道名推断类别标签（纯提示，按固定顺序）。"""
    keys = {str(k) for k in (values or {})}
    return [kind for kind, members in PALETTE_KINDS.items()
            if any(m in keys for m in members)]


def list_palettes() -> List[str]:
    """全部调色板名（按名排序；库缺失/损坏 → 空表）。"""
    try:
        return sorted(str(k) for k in _read_doc()["palettes"])
    except Exception:
        return []


def save_palette(name: str, values: Dict[str, int]) -> bool:
    """存/覆盖命名调色板；名非法或值表空 → False（不写盘）。"""
    nm = str(name or "").strip()
    cleaned = clean_values(values)
    if not nm or not cleaned:
        return False
    doc = _read_doc()
    now = time.time()
    old = doc["palettes"].get(nm) or {}
    doc["palettes"][nm] = {
        "created": float(old.get("created", now) or now),
        "updated": now,
        "values": cleaned,
        "kinds": classify_kinds(cleaned),
    }
    return _write_doc(doc)


def load_palette(name: str) -> Optional[Dict[str, int]]:
    """取单个调色板值表（不存在/损坏 → None；返回深拷贝）。"""
    try:
        entry = _read_doc()["palettes"].get(str(name or "").strip())
    except Exception:
        return None
    if not isinstance(entry, dict):
        return None
    return clean_values(entry.get("values"))


def delete_palette(name: str) -> bool:
    """删除调色板；不存在 → False。"""
    nm = str(name or "").strip()
    doc = _read_doc()
    if nm not in doc["palettes"]:
        return False
    doc["palettes"].pop(nm, None)
    return _write_doc(doc)


def palette_info(name: str) -> Optional[dict]:
    """调色板元信息 `{created, updated, kinds, n_channels}`（不存在 → None）。"""
    try:
        entry = _read_doc()["palettes"].get(str(name or "").strip())
    except Exception:
        return None
    if not isinstance(entry, dict):
        return None
    values = clean_values(entry.get("values"))
    return {
        "created": float(entry.get("created", 0.0) or 0.0),
        "updated": float(entry.get("updated", 0.0) or 0.0),
        "kinds": list(entry.get("kinds", []) or classify_kinds(values)),
        "n_channels": len(values),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  与面板的对接（纯逻辑；写值由调用方走既有通路）
# ─────────────────────────────────────────────────────────────────────────────

def snapshot_channel_values(row_values: Dict[str, Dict[str, int]]) -> Dict[str, int]:
    """把当前组的 `{fid: {ch_type: 值}}` 压成"每通道取代表值"的调色板值表。

    代表值取**出现次数的众数**（同值最广的那个；并列取较大值），这样一组同型
    灯取出的调色板就是"这一组现在的颜色/位置"，而不是被个别异值拉偏的均值。
    """
    tally: Dict[str, Dict[int, int]] = {}
    for _fid, channels in (row_values or {}).items():
        for ct, raw in (channels or {}).items():
            sct = str(ct)
            try:
                val = max(0, min(255, int(round(float(raw)))))
            except (TypeError, ValueError):
                continue
            bucket = tally.setdefault(sct, {})
            bucket[val] = bucket.get(val, 0) + 1
    out: Dict[str, int] = {}
    for ct, bucket in tally.items():
        best = max(bucket.items(), key=lambda kv: (kv[1], kv[0]))
        out[ct] = int(best[0])
    return out


def values_for_fixture(values: Dict[str, int], fd: Any, supports) -> Dict[str, int]:
    """按设备能力过滤值表（`supports(fd, ch_type) -> bool` 由调用方注入）。

    `supports` 传 None → 不过滤（原样返回副本）。dimmer 对纯色灯恒可写（
    Programmer 既有 `_fixture_supports_logical_channel` 已覆盖该规则）。
    """
    cleaned = clean_values(values)
    if supports is None or fd is None:
        return cleaned
    out: Dict[str, int] = {}
    for ct, val in cleaned.items():
        try:
            if supports(fd, ct):
                out[ct] = val
        except Exception:
            continue
    return out


def _owning_row(panel: Any, fid: str, ch_type: str):
    """找 (设备, 通道) 所属的 ChannelTypeRow。

    ★ 写值真源在**行**上（`_set_value` 是 ChannelTypeRow 方法），面板没有同名
      方法 —— 类型分区后同一 ch_type 有多行，必须按 `_fixture_id_set` 精确定位，
      否则写给了不含该设备的行（值静默丢失）。
    """
    for row in (getattr(getattr(panel, "_ch_list", None), "_rows", None) or []):
        if ch_type in (getattr(row, "_ch_types", []) or [])                 and fid in getattr(row, "_fixture_id_set", set()):
            return row
    return None


def _write_values_to_fixtures(panel: Any, fixtures: List[Any], defs: Dict[str, Any],
                              cleaned: Dict[str, int], supports) -> int:
    """把值表按能力过滤后写进给定设备（走行级 `_set_value` 全链路）；返回格数。"""
    written = 0
    for fi in fixtures:
        fid = str(getattr(fi, "id", ""))
        fd = defs.get(getattr(fi, "def_id", ""))
        for ct, val in values_for_fixture(cleaned, fd, supports).items():
            row = _owning_row(panel, fid, ct)
            if row is None:
                continue
            try:
                row._set_value(fid, ct, int(val))
                written += 1
            except Exception:
                continue
    return written


def apply_palette(panel: Any, values: Dict[str, int], supports=None) -> int:
    """把调色板写进**当前组**全部设备（按能力过滤，走既有 `_set_value` 全链路）。

    返回写入格数；面板/组为空 → 0（安全 no-op）。
    """
    cleaned = clean_values(values)
    fixtures = list(getattr(panel, "_fixtures", []) or []) if panel is not None else []
    defs = getattr(panel, "_fixture_defs_cache", {}) or {}
    if not cleaned or not fixtures:
        return 0
    return _write_values_to_fixtures(panel, fixtures, defs, cleaned, supports)


def palette_row_values(panel: Any) -> Dict[str, Dict[str, int]]:
    """取面板当前组的 `{fid: {ch_type: 值}}` 快照。

    取值口径按面板既有真源（`snapshot_static_values` → `_ch_list.get_all_values`），
    与调度台抓取同源 —— 不新造第二条读值链。
    """
    if panel is None:
        return {}
    for owner, name in ((panel, "snapshot_static_values"), (panel, "get_all_values"),
                        (getattr(panel, "_ch_list", None), "get_all_values")):
        getter = getattr(owner, name, None) if owner is not None else None
        if not callable(getter):
            continue
        try:
            snapshot = getter()
        except Exception:
            continue
        if isinstance(snapshot, dict):
            return {str(k): clean_values(v) for k, v in snapshot.items()}
    return {}


def supports_for_place(panel: Any = None):
    """返回 `supports(fd, ch_type) -> bool`（复用面板既有能力检测口径）。"""
    def _supports(fd: Any, ch_type: str) -> bool:
        try:
            from .programmer_panel import _fixture_supports_logical_channel
            return bool(_fixture_supports_logical_channel(fd, ch_type))
        except Exception:
            return False
    return _supports


def apply_palette_to_selection(panel: Any, values: Dict[str, int]) -> int:
    """把调色板写进当前**选中设备**（按设备能力过滤，复用 `_set_value` 全链路）。

    与 `apply_palette` 的区别：后者遍历行内全部 `_fixtures`（= 当前组），本函数
    只写 `_current_fixtures_selected`（= 当前选中）——Palette 语义是"应用到选中"。
    返回写入格数；无选择/空值表 → 0（安全 no-op）。
    """
    cleaned = clean_values(values)
    selected = list(getattr(panel, "_current_fixtures_selected", []) or [])
    defs = getattr(panel, "_fixture_defs_cache", {}) or {}
    if panel is None or not cleaned or not selected:
        return 0
    return _write_values_to_fixtures(panel, selected, defs, cleaned, supports_for_place(panel))

# ─────────────────────────────────────────────────────────────────────────────
#  UI 条：GroupSelector 下方的调色板条（项 8 的最小注入实现）
# ─────────────────────────────────────────────────────────────────────────────

_BAR_QSS = ("QPushButton{background:#2D2D2D;border:1px solid #3D3D3D;"
            "border-radius:3px;color:#CCCCCC;font-size:10px;padding:0 6px;"
            "min-height:20px;}"
            "QPushButton:hover{background:#3A3A3A;color:#FFFFFF;}")


def _palette_bar_reload(combo, apply_btn, del_btn, keep: str = "") -> None:
    """重填调色板下拉（保留 keep；无预置时禁用"应用/删除"）。"""
    names = list_palettes()
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(names)
    if keep and keep in names:
        combo.setCurrentText(keep)
    combo.blockSignals(False)
    apply_btn.setEnabled(bool(names))
    del_btn.setEnabled(bool(names))


def _palette_bar_save(panel, bar, combo, apply_btn, del_btn) -> None:
    """「存为…」：当前组值 → 命名调色板（空组/坏名 → 提示，不写盘）。"""
    from PyQt6.QtWidgets import QInputDialog
    from .programmer_highlight import status as _status
    name, ok = QInputDialog.getText(bar, "保存调色板", "名称：")
    if not ok or not str(name).strip():
        return
    values = snapshot_channel_values(palette_row_values(panel))
    if not values:
        _status(panel, "调色板：当前组没有可保存的通道值")
        return
    if save_palette(name, values):
        _palette_bar_reload(combo, apply_btn, del_btn, str(name).strip())
        _status(panel, f"调色板已保存：{str(name).strip()}（{len(values)} 个通道）")
    else:
        _status(panel, "调色板：保存失败（名称非法或值表为空）")


def _palette_bar_apply(panel, combo) -> None:
    """「应用」：把选中预置写入当前选中设备（按能力过滤）。"""
    from .programmer_highlight import status as _status
    name = combo.currentText()
    values = load_palette(name)
    if not values:
        _status(panel, "调色板：该预置不存在或为空")
        return
    n = apply_palette_to_selection(panel, values)
    _status(panel, f"调色板已应用：{name}（写入 {n} 格）" if n
            else "调色板：当前没有选中设备（或设备均不支持这些通道）")


def build_palette_bar(panel: Any) -> Any:
    """调色板条：`[存为…] [应用] [删除] <名字下拉>`（挂在 GroupSelector 上方）。

    UI 构造器（不是上帝类方法）：在 `ProgrammerPanel._build_ui` 里以一行注入调用；
    存储/应用逻辑走本模块既有 API。条上挂 `reload_palettes()` 供外部刷新。
    """
    from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QWidget
    bar = QWidget()
    bar.setObjectName("programmerPaletteBar")
    bar.setToolTip("调色板：把当前选中设备的值存成命名预置，之后一键复用到别的组")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(6, 2, 6, 2)
    lay.setSpacing(4)
    combo = QComboBox()
    combo.setFixedHeight(22)
    combo.setToolTip("已存调色板（%APPDATA%/Qascade/programmer_palettes.json）")
    combo.setMinimumWidth(90)
    save_btn = QPushButton("存为…")
    save_btn.setStyleSheet(_BAR_QSS)
    save_btn.setToolTip("把当前组设备的通道值存成命名调色板")
    apply_btn = QPushButton("应用")
    apply_btn.setStyleSheet(_BAR_QSS)
    apply_btn.setToolTip("把选中调色板写入当前**选中设备**（按设备能力过滤）")
    del_btn = QPushButton("删除")
    del_btn.setStyleSheet(_BAR_QSS)
    del_btn.setToolTip("删除选中的调色板")
    for w in (combo, save_btn, apply_btn, del_btn):
        lay.addWidget(w)
    reload_fn = lambda keep="": _palette_bar_reload(combo, apply_btn, del_btn, keep)
    save_btn.clicked.connect(lambda: _palette_bar_save(panel, bar, combo, apply_btn, del_btn))
    apply_btn.clicked.connect(lambda: _palette_bar_apply(panel, combo))
    del_btn.clicked.connect(lambda: _palette_bar_delete(panel, combo, reload_fn))
    reload_fn()
    bar.reload_palettes = reload_fn
    return bar


def _palette_bar_delete(panel, combo, reload_fn) -> None:
    """「删除」：删掉选中预置并重填下拉。"""
    from .programmer_highlight import status as _status
    name = combo.currentText()
    if name and delete_palette(name):
        reload_fn()
        _status(panel, f"调色板已删除：{name}")
