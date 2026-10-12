# -*- coding: utf-8 -*-
"""R141 时间线交互性能工具（模块级，避免撑大既有超限文件）。

三件事，都是把"随项目/工程总量线性"的热路径换成 O(1)/O(选中)：

1. `fast_scoped_snapshot` —— 手势级撤销快照只序列化 domain 内字段。
   旧路径 `ProjectManager._snapshot_scoped` 把**整工程** to_dict 后 json.dumps：
   真工程 `E:\\水仙十字.dmxt` 载荷 78.3 MB（其中 74.6 MB 是拖 clip 根本不碰的
   `dispatch_state`），单次手势快照实测 **780 ms**。同一工程切片后 8 ms。
   undo 语义逐位不变：`ProjectManager._merged_project` 只读 domain 内字段。

2. `views_owning_clip` —— `ClipArea.refresh_clip(clip_id)` 的 O(1) 定位。
   旧实现对**每个视图**调 `refresh_geometry_only(clip_id)`；非属主视图走到
   "无 id 全量"分支 → 每帧对全轨道 item 重算 geometry + 重绘（O(N)/帧）。

3. `ClipEndCache` —— `TimelineWidget._content_end_time` 的 clip 尾端最大值
   O(1) 增量缓存。旧实现每帧（clip_changed → _sync_timeline_duration）全量扫描
   所有轨道的所有 clip。
"""
from __future__ import annotations

import json

from PyQt6.QtCore import QTimer
from ...core.timeline_snapshot import timeline_snapshot_dict

# Compression is deliberately deferred past the current edit burst.  The
# uncompressed JSON entry is already a valid undo payload; doing zlib work in
# the next event-loop turn made dense timelines stutter on the first drag move.
_SCOPED_COMPRESS_DELAY_MS = 250

__all__ = ["scoped_payload_slice", "fast_scoped_snapshot",
           "views_owning_clip", "ClipEndCache", "clip_end_time",
           "audio_ref_end", "wire_track_moved", "adjacent_index",
           "timeline_gesture_snapshot", "duplicate_many", "refresh_owning_views",
           "prime_scoped_snapshot_cache", "schedule_scoped_snapshot_prime",
           "invalidate_scoped_snapshot_cache"]

_SNAPSHOT_CACHE_ATTR = "_timeline_scoped_snapshot_cache"
_SNAPSHOT_PRIME_PENDING_ATTR = "_timeline_scoped_snapshot_prime_pending"


# ── 4. 上下移轨道（★ R141 修 bug ⑧）────────────────────────────────────────

def adjacent_index(tracks, track_id, delta):
    """`track_id` 沿扁平顺序移动 `delta` 格后的目标下标；不可移动返回 None。

    边界（首位上移 / 末位下移）与未知 id 都返回 None → 调用方不写模型、
    不压撤销、不广播（"点了没反应"只应发生在真的无处可移时）。
    """
    try:
        idx = next(i for i, t in enumerate(tracks) if t.id == track_id)
    except StopIteration:
        return None
    target = idx + int(delta)
    if target == idx or target < 0 or target >= len(tracks):
        return None
    return target


def wire_track_moved(track_list, handler) -> None:
    """把 `TrackListPanel.track_moved` 接到 `handler`（★ R141 修 bug ⑧）。

    右键「上移轨道 / 下移轨道」此前完全没反应：`track_list.py:683` 把
    `TrackHeader.track_moved` 转发成 `TrackListPanel.track_moved`，但全仓
    没有任何一处 connect 它 → 信号发出去无人接。列表/处理器缺失时安全降级。
    """
    if track_list is None or handler is None:
        return
    signal = getattr(track_list, "track_moved", None)
    if signal is None:
        return
    try:
        signal.connect(handler)
    except Exception:
        pass


# ── 0. 音频参考轨尾端 ─────────────────────────────────────────────────────

def audio_ref_end(audio_track) -> float:
    """音频参考轨的结束时间（`clip_end_time` 优先，为 0 时退回 `_duration`）。

    原为 `TimelineWidget._content_end_time` 的内联两段 try/except；抽成模块级
    函数让 `_content_end_time` 留在架构基线的行数/CC 之内（门禁只许缩小）。
    取值语义逐字保持不变。
    """
    try:
        end = float(getattr(audio_track, "clip_end_time", 0.0) or 0.0)
    except Exception:
        end = 0.0
    if end <= 0.0:
        try:
            end = float(getattr(audio_track, "_duration", 0.0) or 0.0)
        except Exception:
            end = 0.0
    return end


# ── 1. 手势级撤销快照：只序列化 domain 内字段 ─────────────────────────────

def scoped_payload_slice(project, domain) -> str:
    """`domain` 内字段的 JSON 载荷（取值与全量快照同源，只是不再带上无关字段）。"""
    full = timeline_snapshot_dict(project, domain)
    return json.dumps({k: full[k] for k in sorted(domain) if k in full},
                      ensure_ascii=False, separators=(",", ":"))


def invalidate_scoped_snapshot_cache(pm) -> None:
    """Drop the idle snapshot after a timeline mutation or project rebind."""
    if pm is not None:
        try:
            setattr(pm, _SNAPSHOT_CACHE_ATTR, None)
        except RuntimeError:
            # A queued rebuild can outlive ProjectManager's C++ QObject.
            pass


def prime_scoped_snapshot_cache(pm, domain) -> bool:
    """Capture the current undo payload during idle time, without pushing undo.

    The first drag must still snapshot *before* it mutates a Clip.  Building
    that JSON synchronously on the first mouse move is what made the cost grow
    with the number of clips.  An idle copy is safe because the payload is
    immutable and is discarded whenever the owning timeline is marked dirty.
    """
    if pm is None or not domain:
        return False
    try:
        project = getattr(pm, "_project", None)
        if not project:
            return False
        payload = scoped_payload_slice(project, domain)
        refs = pm._collect_frames_refs()
        setattr(pm, _SNAPSHOT_CACHE_ATTR, (project, frozenset(domain), payload, refs))
        return True
    except Exception:
        invalidate_scoped_snapshot_cache(pm)
        return False


def schedule_scoped_snapshot_prime(pm, domain) -> None:
    """Refresh the idle cache once after the current edit burst."""
    if pm is None or not domain:
        return
    key = frozenset(domain)
    pending = getattr(pm, _SNAPSHOT_PRIME_PENDING_ATTR, None)
    if not isinstance(pending, set):
        pending = set()
    if key in pending:
        return
    pending.add(key)
    setattr(pm, _SNAPSHOT_PRIME_PENDING_ATTR, pending)

    def _prime() -> None:
        try:
            pending.discard(key)
            prime_scoped_snapshot_cache(pm, domain)
        except RuntimeError:
            # The timeline window may be rebuilt before the idle callback runs.
            pass

    try:
        QTimer.singleShot(300, _prime)
    except Exception:
        _prime()


def _scoped_snapshot_payload(pm, domain):
    """Return an idle payload when it still belongs to the live project."""
    cached = getattr(pm, _SNAPSHOT_CACHE_ATTR, None)
    project = getattr(pm, "_project", None)
    if (cached is not None and cached[0] is project
            and cached[1] == frozenset(domain)):
        return cached[2], cached[3]
    return scoped_payload_slice(project, domain), pm._collect_frames_refs()


def _push_scoped_snapshot(pm, entry) -> None:
    """Push one undo entry with the same trim/redo semantics as ProjectManager."""
    stack = pm._undo_stack
    stack.append(entry)
    if len(stack) > pm._MAX_UNDO:
        stack.pop(0)
    pm._redo_stack.clear()


def _schedule_scoped_compression(pm, entry) -> None:
    """Compress after the edit burst, with a synchronous fallback for tests."""
    def _compress() -> None:
        try:
            pm._compress_scoped_entry(entry)
        except RuntimeError:
            # A delayed compression must not touch a deleted ProjectManager.
            pass

    try:
        QTimer.singleShot(_SCOPED_COMPRESS_DELAY_MS, _compress)
    except Exception:
        try:
            _compress()
        except Exception:
            pass


def fast_scoped_snapshot(pm, domain) -> bool:
    """压一条轻量 scoped 快照（条目形状与 `pm._snapshot_scoped` 完全一致）。

    条目 = ("scoped", payload, frames_refs, domain)；trim / 清 redo / 延迟压缩
    三步与 PM 同口径。任何一步不可用（老 PM、测试替身、异常）返回 False，
    调用方回退 `pm._snapshot_scoped`，绝不静默丢快照。
    """
    if pm is None or not domain:
        return False
    try:
        payload, refs = _scoped_snapshot_payload(pm, domain)
        entry = ("scoped", payload, refs, domain)
        _push_scoped_snapshot(pm, entry)
    except Exception:
        return False
    _schedule_scoped_compression(pm, entry)
    return True


def timeline_gesture_snapshot(pm, domain) -> bool:
    """手势级撤销快照：domain 切片 → `_snapshot_scoped` → `_snapshot` 逐级回退。

    `domain` 由调用方（时间线各入口）**显式声明** —— R131 语义：只回滚这一步
    真正动过的字段。任一级不可用都往后退，绝不静默丢快照。返回是否已压栈。
    """
    if pm is None or not domain:
        return False
    try:
        if fast_scoped_snapshot(pm, domain):
            return True
        if hasattr(pm, "_snapshot_scoped"):
            pm._snapshot_scoped(domain=domain)
            return True
        if hasattr(pm, "_snapshot"):
            pm._snapshot(domain=domain)
            return True
    except Exception:
        return False
    return False


def duplicate_many(ctrl, target_ids) -> list:
    """多选重复片段：**先快照一次**，再逐个纯复制（★ R141 修 bug ⑦）。

    `duplicate_clip` 默认每次都自动快照；循环调用会让"选中 N 个重复一次"产生
    N 条撤销记录（要按 N 次 Ctrl+Z），故循环里传 `snapshot=False`，与多选删除
    同一口径。先探测"确有可复制 clip"再快照，保持旧实现"全为空时不压栈"。
    """
    ids = list(target_ids or [])
    if not ids:
        return []
    if not any(ctrl._find_track_and_clip(cid)[1] is not None for cid in ids):
        return []
    ctrl._snapshot_for_undo()
    created = []
    for cid in ids:
        clip = ctrl.duplicate_clip(cid, snapshot=False)
        if clip is not None:
            created.append(clip.id)
    return created


# ── 2. 精准定位 clip 所属视图 ─────────────────────────────────────────────

def views_owning_clip(views, clip_id):
    """返回包含 `clip_id` 的视图列表；无人认领时回退**全部视图**（旧行为）。

    回退是刻意的：裁剪/隐藏轨道上的 clip 不在任何视图里，此时旧实现会让每个
    视图走"全量几何刷新"分支，必须保持一致（宁可多刷不可漏刷）。
    """
    if not clip_id:
        return list(views)
    cid = str(clip_id)
    owner = [v for v in views if cid in (getattr(v, "_items", None) or {})]
    return owner or list(views)


def refresh_owning_views(container, clip_id, reset_waveform: bool = False) -> None:
    """只刷新**拥有** `clip_id` 的视图（★ R141 修 `ClipArea.refresh_clip` 的全量扫）。

    旧实现（`_TrackContainer.refresh_clip`）对每个视图都调
    `refresh_geometry_only(clip_id, ...)`：非属主视图命中"无匹配 id → 全量"分支 →
    每帧对**全部轨道全部 item** 重算 geometry + 重绘（400 clip 工程单帧主要来源）。
    clip 只可能属于一个视图，属主之外无物可刷（几何不变，重绘结果逐位相同）。
    `reset_waveform=True` 是"帧数据被原地重写"的失效请求，语义上要求全视图清缓存，
    故保留旧的全量扫描。容器缺失时回退旧入口。
    """
    views = list(getattr(container, "_views", None) or []) if container is not None else []
    if not views:
        if container is not None:
            container.refresh_clip(clip_id, reset_waveform=reset_waveform)
        return
    if reset_waveform:
        for v in views:
            v.refresh_geometry_only(clip_id, reset_waveform=True)
        return
    for v in views_owning_clip(views, clip_id):
        v.refresh_geometry_only(clip_id)


# ── 3. clip 尾端最大值缓存 ────────────────────────────────────────────────

def clip_end_time(clip):
    """clip 的 `start + max(0, duration)`；取值失败返回 None（与旧实现同口径跳过）。"""
    try:
        start = float(getattr(clip, "start_time", 0.0) or 0.0)
        dur = float(getattr(clip, "duration", 0.0) or 0.0)
    except Exception:
        return None
    return start + max(0.0, dur)


def _live_tracks(project):
    return list(getattr(project, "tracks", None) or [])


class ClipEndCache:
    """live 轨道里 clip 结束时间的最大值（`_content_end_time` 的扫描部分）。

    命中 O(1)；只有"当前最大值的那个 clip 变短了"或"出现未知 clip"才失效，
    下一次查询做一遍 O(N) 重建（与旧实现单次扫描等价）。

    刻意**不用 `__slots__`**：`note_clip_changed` 要直接接到 pyqtSignal 上，
    而 PyQt6 对非 QObject 接收者持弱引用，无 `__weakref__` 槽时连接会抛
    SystemError（实测）。
    """

    def __init__(self):
        self._max_end = None
        self._argmax_id = ""
        self._index: dict = {}

    def invalidate(self):
        self._max_end = None
        self._index = {}

    @property
    def valid(self) -> bool:
        return self._max_end is not None

    def note_clip_changed(self, clip_id):
        """`clip_changed(clip_id)` 时 O(1) 更新；不能安全更新则失效。"""
        if self._max_end is None:
            return
        cid = str(clip_id or "")
        clip = self._index.get(cid)
        if clip is None:
            self.invalidate()
            return
        end = clip_end_time(clip)
        if end is None:
            self.invalidate()
        elif end > self._max_end:
            self._max_end = end
            self._argmax_id = cid
        elif cid == self._argmax_id and end < self._max_end:
            self.invalidate()

    def value(self, project) -> float:
        if self._max_end is None:
            self._rebuild(project)
        return 0.0 if self._max_end is None else self._max_end

    def _rebuild(self, project) -> None:
        index: dict = {}
        best = 0.0
        argmax = ""
        for track in _live_tracks(project):
            for clip in (getattr(track, "clips", None) or []):
                cid = str(getattr(clip, "id", "") or "")
                index[cid] = clip
                end = clip_end_time(clip)
                if end is not None and end > best:
                    best, argmax = end, cid
        self._index = index
        self._max_end = best
        self._argmax_id = argmax
