# -*- coding: utf-8 -*-
"""场景**后发接管**：把"刚点亮的场景"在 NetworkManager 的 LTP 仲裁里置为最新。

用户 2026-09-18 实报（问题7 第二层）：
  "依旧存在我报告的那个**先 programmer 后 effect** 等情况（这个只是一种），
   也就是**所有情况下都没完全满足后发接管**，但按照顺序依次还原的设计。"
  "如果多开到**同一个轨道**，遵循跟 clip 轨道一样的**后发接管**原则，
   并且注意**依次恢复性缓存**。"

实测根因（真实路径探针 `_ui_validation/_takeover3.py`，原始数据见 `_takeover3.log`）：

  · 不同面板写的是**不同的混音层**（programmer→`fader`、effect→`effect`、
    matrix→`matrix`/`matrix_audio`），层间靠 NM 的 **LTP 时间戳**仲裁
    （`_mix_one_universe`：base 与 shape 谁的时间新谁赢）。
  · 但"时间戳是新的"只在**两条路径**成立：面板自己给用户操作打的
    `_ltp_takeover_pending`（点应用/改参数），以及 programmer 落值 flush 时的
    默认 `touch_ltp=True`。**场景调用（recall）这条路却不 stamp**
    （effect 的 `dispatch_recall_state` 新建 state、没有 `_ltp_takeover_pending`；
    matrix 的 `_matrix_ltp_takeover_pending` 早被消费掉）。
  · 结果：programmer 场景（fader）先亮，再点 effect 场景 → effect 层的值虽然写进
    NM，却没有新时间戳 → 混音仍判 `base_newer` → **输出还是 fader 的旧值**
    （"effect 根本没接管"）；matrix 类似，且时序不同结果不同。

修法（本模块，纯 dispatch 侧、零面板改动）：**点亮即重 stamp** —— 场景 recall 之后，
把它各节点**真实写着值的输出层**在这些地址上的当前值**原样重写一次**
（`update_layer_sparse(..., touch_ltp=True)`）。这不会改动任何数值，只把该层在这些
通道上的 LTP 时间戳推到"现在"，于是：

  · 后点亮的场景**必然赢**同轨仲裁（任意面板组合、任意先后顺序）；
  · 先点亮的场景**仍在自己的层里**（值没被擦、面板照常跑、高亮不熄），
    后点亮的场景一被释放，它自然重新出光 —— 这就是"依次还原"；
  · **不同轨道**（地址不相交）的场景互不影响，天然并存。

为什么不做"激活时释放旧场景让位"：panel 的释放是**整层清空**
（programmer 清 fader/programmer/tester/audio/joystick、effect 清 effect、
matrix 清 matrix/matrix_audio），会把**同层别的轨道**的场景一起擦掉；而且
matrix 的 recall 目前**不起输出**（见下面 `_RELEASE_CLEARS` 说明），
让位后再回放也回不来。LTP 重 stamp 无此风险。

层→地址的换算复用调度台既有真源 `processing.dispatch.fixture_table`（
监控区取值走的同一张设备信息表），不新造第二套地址语义。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Set, Tuple

#: 面板 → 它**真正写出**的混音层（按 `core.constants.ALL_LAYERS` 的子集）。
#: ★ programmer 的节点自报 layers 是 ["programmer"]，但它实际写的是 **fader**
#:   （同一张对应关系也见 `scene_fade_ops._PRIMARY_LAYER`）；scene recall 只落 fader。
#:   `programmer` 仍一并列出：该层名在 NM 里合法（旧工程/旧路径可能落它），
#:   stamp 只写"该层确实有值的地址"，多列无害。
#: ★ matrix 按"是否音频响应"在 matrix / matrix_audio 之间切换，故两层都看。
#: ★ 只列"场景 recall 会落值"的层：不推广到 audio/joystick 等旁路层。
_OUTPUT_LAYERS: Dict[str, Tuple[str, ...]] = {
    "programmer": ("fader", "programmer"),
    "effect": ("effect",),
    "matrix": ("matrix", "matrix_audio"),
    "audio": ("audio",),
    "linker": ("linker",),
}

#: 面板 `dispatch_release_state` **整层清空**的范围（"依次还原"的受损判据，供
#: 诊断/未来扩展；当前接管实现不依赖它）。与实现一一对应：
#:   · programmer → `_clear_programmer_layers`（`_PROG_CLEAR_LAYERS`）
#:   · effect     → `_on_stop` 的 `clear_layer("effect")`（切片模式保留最后帧，例外）
#:   · matrix     → `_clear_matrix_layers`（matrix + matrix_audio）
#:   · audio/linker 不整层清空（只停自己的输出）→ 不会波及其它场景
_RELEASE_CLEARS: Dict[str, Tuple[str, ...]] = {
    "programmer": ("fader", "programmer", "tester", "audio", "joystick"),
    "effect": ("effect",),
    "matrix": ("matrix", "matrix_audio"),
    "audio": (),
    "linker": (),
}

Addr = Tuple[int, int]


# ── R178：stamp 台账已拆到 scene_stamp_ledger（本文件 500 行门禁）；原名重导出，
#    scene_release/tests 的 `from .scene_takeover import clear_stamp_ledger` 等零改动。──
from .scene_stamp_ledger import (  # noqa: F401
    _STAMP_LEDGER, _blocked_addrs, _note_stamped, _scene_priority,
    _stamp_layer, _stamp_node, clear_stamp_ledger, note_scene_released,
    stamp_scene_takeover,
)


def _nm_of(panel):
    """调度台注入的 NetworkManager（未注入/取不到 → None）。"""
    try:
        provider = getattr(panel, "_nm_provider", None)
        nm = provider() if callable(provider) else None
        return nm if nm is not None and hasattr(nm, "update_layer_sparse") else None
    except Exception:
        return None


def _entries_of(cid: str) -> List[dict]:
    """场景条目（含 node + state）；取不到给空列表。"""
    try:
        from .store_area_ops import _entries_of as _impl
        return [dict(e) for e in (_impl(cid) or [])]
    except Exception:
        return []


def _nodes_of(cid: str) -> List[dict]:
    """场景条目 → 节点 dict 列表（取不到给空列表）。"""
    return [dict((e or {}).get("node", {}) or {}) for e in _entries_of(cid)]


def _panels_of(cid: str) -> Set[str]:
    """该场景涉及的面板 id 集合。"""
    return {str((n or {}).get("panel", "") or "") for n in _nodes_of(cid)}


def scene_output_layers(cid: str) -> Set[str]:
    """该场景真正会用到的输出层（按节点所属面板映射；未知面板给空）。"""
    out: Set[str] = set()
    for panel in _panels_of(cid):
        out |= set(_OUTPUT_LAYERS.get(panel, ()))
    return out


def release_cleared_layers(cid: str) -> Set[str]:
    """释放该场景会**整层清空**的层集合（"依次还原"的受损判据；见 `_RELEASE_CLEARS`）。"""
    out: Set[str] = set()
    for panel in _panels_of(cid):
        out |= set(_RELEASE_CLEARS.get(panel, ()))
    return out


def _addrs_for_types(item: dict, ch_types: List[str]) -> Set[Addr]:
    """该设备在给定值类型上的全部地址（含虚拟 dimmer 展开到灯色地址）。"""
    from ...processing.dispatch import (channel_addrs_for, dimmer_source_addrs,
                                        is_virtual_dimmer, norm_channel_type)
    out: Set[Addr] = set()
    for ct in ch_types:
        out |= set(channel_addrs_for(item, ct))
    if any(norm_channel_type(ct) == "dimmer" for ct in ch_types) \
            and is_virtual_dimmer(item, "dimmer"):
        out |= set(dimmer_source_addrs(item))
    return out


def _all_addrs(item: dict) -> Set[Addr]:
    """该设备**全部**通道地址（节点未声明 ch_types 时的保守取值）。"""
    from ...processing.dispatch import dimmer_source_addrs
    out: Set[Addr] = set()
    for addrs in (item.get("channel_addrs") or {}).values():
        out |= set(addrs)
    out |= set(dimmer_source_addrs(item))      # 虚拟 dimmer 的取样地址（重复无害）
    return out


def _fixture_addrs(info: dict, fid: str, chs: List[str]) -> Set[Addr]:
    """一台设备在某组值类型（空 = 全部通道）上的地址集（信息缺失 → 空集）。"""
    item = (info or {}).get(str(fid))
    if not item:
        return set()
    return _addrs_for_types(item, chs) if chs else _all_addrs(item)


def _value_fixture_ids(panel_id: str, state) -> Set[str]:
    """该场景**确实写了值**的设备集；空集 = 无从判定（调用方保持原作用域）。

    ★★ 2026-09-18 回访（stamp 误伤其它轨）：programmer 节点的 `fixture_ids` 来自
      保存时的**设备选择**，而 `static_values` 只含真正写过的格 —— 用户在组里
      选中 5 台、只改了其中 3 台时，节点作用域 **宽于** 该场景的实际输出。
      于是 stamp 会连"该场景根本没有值"的地址一起推时间戳；那些地址上若还留着
      别的场景（同层残留 / 另一个 programmer 场景）的值，就等于替**别的场景**
      刷新 LTP → 把正在出光的 effect/matrix 从该通道上挤掉。
      实测（生产路径、无任何注入）：B=programmer(f0=66) → E=effect(f0) 抢到 f0 →
      点 C（作用域 f0+f1、值只有 f1）→ f0 输出变回 **66**（关掉 stamp 则仍是
      effect 的 137）。故这里只对能判定写值集的 programmer 收窄。
      其它面板（effect/matrix）写值集无法从存档判定 → 返回空集（保持原行为）。
    """
    panel_id = str(panel_id or "")
    if panel_id == "programmer":
        vals = (state or {}).get("static_values") or {}
        try:
            return {str(f) for f, chs in vals.items() if chs}
        except Exception:
            return set()
    if panel_id == "effect":
        # ★★ R138 用户实报（"调用后效果直接没了/被默认值压死"）：effect 节点的
        #   `fixture_ids` 来自**保存时的设备选择**，而 `state.active_effects` 里
        #   才记着"这一格当时真正驱动的设备集"。不收窄就会把该 effect 没在驱动的
        #   地址也 stamp 一遍 —— 那些地址上若留着别的层（典型：programmer 的
        #   电机归中 128）的残留，就等于**替那层刷新 LTP**，把本场景的 effect
        #   从这些通道上挤掉（实测：16 台电机全变 128，波形整段消失）。
        return _effect_value_ids(state)
    return set()


def _effect_value_ids(state) -> Set[str]:
    """effect 场景存档里**各格真正驱动的设备集**（`active_effects[*].group_ids`）。

    取并集（一个场景可含多格；调用时每格都会写自己的层）——
    `active_effects` 缺失（旧档/非 effect 面板）→ 空集 = 无从判定，保持原作用域。
    """
    out: Set[str] = set()
    for a in ((state or {}).get("active_effects") or ()):
        if not isinstance(a, dict):
            continue
        try:
            out.update(str(x) for x in (a.get("group_ids") or []) if str(x))
        except Exception:
            continue
    return out


def _scope_addrs(panel, node: dict, state=None) -> Set[Addr]:
    """该节点在工程里的 DMX 地址集 `{(域, 地址)}`（设备信息表不可用 → 空集）。

    ★ `ch_types` 为空 = 该节点未声明通道作用域 → 取该设备的**全部**通道地址
      （宁可多 stamp 也不能漏：漏了就等于没接管）。
    ★ `state` 提供时，能判定"确实写过值的设备"的面板（programmer）按其收窄 ——
      见 `_value_fixture_ids`（防止替别的场景刷新时间戳、抢走不属于它的通道）。
    """
    try:
        info = panel._fixture_info()
    except Exception:
        return set()
    if not info:
        return set()
    if node.get("panel") == "matrix":
        from ..matrix_slideshow.matrix_channel_nodes import matrix_scope_addresses
        return matrix_scope_addresses(info, node, state)
    chs = [str(x) for x in (node.get("ch_types") or []) if str(x)]
    ids = [str(x) for x in (node.get("fixture_ids") or []) if str(x)]
    value_ids = _value_fixture_ids(node.get("panel", ""), state)
    if value_ids:
        ids = [fid for fid in ids if fid in value_ids]
    out: Set[Addr] = set()
    for fid in ids:
        out |= _fixture_addrs(info, fid, chs)
    return out


def _by_universe(addrs: Iterable[Addr]) -> Dict[int, List[int]]:
    """`{(域, 地址)}` → `{域: [地址…]}`（NM 按域批量接口用）。"""
    out: Dict[int, List[int]] = {}
    for item in (addrs or ()):
        try:
            uni, addr = int(item[0]), int(item[1])
        except Exception:
            continue
        out.setdefault(uni, []).append(addr)
    return out

def _node_addrs_to_stamp(panel, cid: str) -> List[Tuple[dict, Set[Addr]]]:
    """该场景各节点 → `(node, 要 stamp 的地址集)`（地址空者剔除）。"""
    out: List[Tuple[dict, Set[Addr]]] = []
    for entry in _entries_of(cid):
        node = dict((entry or {}).get("node", {}) or {})
        state = dict((entry or {}).get("state", {}) or {})
        addrs = _scope_addrs(panel, node, state)
        if addrs:
            out.append((node, addrs))
    return out


# ── 输出补启动（矩阵场景 recall 不起输出 —— 实测缺口）───────────────────────
def _widget_for(panel, panel_id: str):
    """按面板 id 取面板实例（未注入 `panel_provider` → None）。"""
    try:
        provider = getattr(panel, "_panel_provider", None)
        return provider(panel_id) if callable(provider) else None
    except Exception:
        return None


def _matrix_outputting(widget) -> bool:
    """矩阵面板当前是否**真的在出光**（像素在跑 / PPT 引擎在播）。

    ★ 刻意**不看播放按钮的勾选态**：按钮勾着但传输已暂停（`_pixel_paused`）时面板
      并不写层，而"点场景"期望的是画面动起来 —— 那种情况要按未出光处理并复位传输
      （见 `_start_matrix_output` 的先关后开）。
    """
    try:
        if bool(getattr(widget, "_pixel_running", False)):
            return True
        eng = getattr(widget, "_engine", None)
        return bool(getattr(eng, "is_playing", False))
    except Exception:
        return True                       # 判不出来 → 不插手（宁可不启动）


def _recall_again(panel, entries: List[dict]) -> bool:
    """把 entries 再 recall 一遍（矩阵补启动的第二遍，写出一帧）。"""
    recall = getattr(panel, "_recall_nodes", None)
    if not callable(recall):
        return False
    payload = [dict(e) for e in (entries or [])]
    try:
        recall(payload, sync_selection=False)
        return True
    except TypeError:                       # 旧签名注入
        try:
            recall(payload)
            return True
        except Exception:
            return False
    except Exception:
        return False


def _matrix_start_playback(widget) -> bool:
    """按用户动作「点播放」启动矩阵输出；返回是否已按下播放。

    ★ 先**取消**再按下：面板的 `_on_play_toggled(False)` 会走 `_pause_pixel_mode`
      （保留层、记 `_pixel_paused`），再按下时 `_start_pixel_mode` 以 `was_paused`
      恢复相位 —— 于是"按钮勾着但其实暂停/未跑"的传输也能被正确唤醒（`setChecked(True)`
      对已勾选的按钮是空操作，唤不醒）。
    """
    btn = getattr(widget, "_play_btn", None)
    if btn is None:
        return False
    try:
        if btn.isChecked():
            btn.setChecked(False)
    except Exception:
        return False
    try:
        btn.setChecked(True)
        return bool(btn.isChecked())
    except Exception:
        return False


def _start_matrix_output(panel, entries: List[dict]) -> int:
    """矩阵场景 recall 后**补启动**它的输出（见 `_MATRIX_RECALL_GAP`）。

    做法 = 用户动作「点播放」（`_play_btn` → 面板自己的 `_on_play_toggled`
    → `_start_pixel_mode` / PPT 播放），**再把这个场景的矩阵节点 recall 一次**
    —— 这一遍才真正写出一帧（`_tick` 在 `_pixel_running=False` 时一行都不写），
    并把面板的"只是被调用带活的"标记（`_recalled_play`）落回 True（启动播放会把它清掉，
    而它决定"保存新场景时是否排除这一格"，必须由调用方在此补齐）。
    """
    widget = _widget_for(panel, "matrix")
    if widget is None or _matrix_outputting(widget):
        return 0
    if not _matrix_start_playback(widget):
        return 0
    return 1 if _recall_again(panel, entries) else 0


#: 面板 → "recall 之后仍需补启动输出"的修补器（当前仅 matrix，见 `_MATRIX_RECALL_GAP`）。
_OUTPUT_STARTERS = {"matrix": _start_matrix_output}


def _mine(entries: List[dict], panel_id: str) -> List[dict]:
    """entries 里属于该面板的那些（保持原序）。"""
    return [e for e in entries
            if str((dict(e.get("node") or {})).get("panel", "") or "") == panel_id]


def ensure_scene_output(panel, cid: str) -> int:
    """确保该场景**真的在出光**（补启动那些 recall 不起输出的面板）。返回补启动数。

    ★★ `_MATRIX_RECALL_GAP`（实测，见 `_ui_validation/_matrix_recall.log`）：
      matrix 的 `dispatch_recall_state` 只做 `_recall_scope` + `_tick()`，而 `_tick`
      在 `_pixel_running=False` 时**一行都不写** → 存场景时"存后自动清台"已把矩阵
      面板停掉（`_pixel_running=False`）→ 再点这个场景，模式/参数都还原了，**输出层
      恒空**（"matrix 场景点了没反应"，三对组合里凡涉及 matrix 都无法成立）。
      该缺口在矩阵面板内部（`src/ui/matrix_slideshow/`，非本次改动范围）→ 由调度台
      按"用户点播放"的既有动作补齐（本函数），面板语义不被改写。
    """
    cid = str(cid or "")
    if not cid:
        return 0
    entries = _entries_of(cid)
    if not entries:
        return 0
    started = 0
    for panel_id, starter in _OUTPUT_STARTERS.items():
        mine = _mine(entries, panel_id)
        if mine:
            started += int(starter(panel, mine) or 0)
    return started


def activate_scene_takeover(panel, cid: str) -> None:
    """场景**点亮后**的接管收口：① 先确保它在出光；② 再打 LTP 时间戳（后发接管）。

    顺序不可颠倒：stamp 读的是"该层当前值"，层里没有值就无从 stamp（矩阵尤其如此）。
    """
    cid = str(cid or "")
    if not cid:
        return
    try:
        ensure_scene_output(panel, cid)
    except Exception:
        pass
    try:
        stamp_scene_takeover(panel, cid)
    except Exception:
        pass


__all__ = ["stamp_scene_takeover", "ensure_scene_output", "activate_scene_takeover",
           "scene_output_layers", "release_cleared_layers",
           "note_scene_released", "clear_stamp_ledger"]
