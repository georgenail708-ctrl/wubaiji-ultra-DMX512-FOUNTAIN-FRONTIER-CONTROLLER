"""
Qascade V1.0 — Pump Precharge Scheduler (A3)
预充电调度器：在泵动作前向泵通道发送预充电 DMX 值（~5Hz 等效），消除 VFD 启动延迟。

泵动作前 ~1 秒向 VFD 发送「就绪频率」（5Hz+），消除启动延迟。
PrechargeLevel = base + param2/10，其中 base = 减量，param2 = 实际放大倍数×10。
默认 level=13 对应约 5Hz（DMX 13/255 × 100% ≈ 5.1% 占空比）。
"""
from __future__ import annotations

import bisect
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from ..core.models import Clip, ChannelType

log = logging.getLogger(__name__)

#: 前瞻注册窗口（秒）：把 `start_time` 落在 `[now, now + 本值]` 的 clip 一并
#: 纳入注册候选。必须 ≥ 任何 clip 的 `precharge_lead_ms / 1000`，注册才会发生在
#: 触发窗口 `[start-lead, start)` 之前（见 `register_due_clips`）。
PRECHARGE_LOOKAHEAD_S = 5.0

#: `precharge_lead_ms` 缺失时的默认提前量（毫秒），与 `PrechargeScheduler.register` 同源。
_DEFAULT_LEAD_MS = 1000.0


def _lead_seconds(clip) -> float:
    """clip 的预充电提前量（秒）。0 是合法值，不能用 `or` 兜底（R131）。"""
    raw = getattr(clip, "precharge_lead_ms", _DEFAULT_LEAD_MS)
    try:
        return max(0.0, (float(_DEFAULT_LEAD_MS if raw in (None, "") else raw)) / 1000.0)
    except (TypeError, ValueError):
        return _DEFAULT_LEAD_MS / 1000.0


def _start_seconds(clip) -> float:
    try:
        return float(getattr(clip, "start_time", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _lookahead_clips(engine, now: float, step: float) -> List[object]:
    """即将进入（或已进入）触发窗口、但**尚未**出现在活跃集里的 clip。

    ★ 用引擎已有的播放索引 `active_events / active_starts`（按 start_time 排序，
      与 clip 布局签名同寿）：`bisect` 取窗口内的少数几项 → 每帧 O(log n + k)，
      不做全 clip 扫描。索引缺失（首帧/烘焙帧路径）时返回空列表 = 退回旧行为。
    """
    index = getattr(engine, "_playback_active_index", None)
    starts = index.get("active_starts") if isinstance(index, dict) else None
    events = index.get("active_events") if isinstance(index, dict) else None
    if not starts or not events:
        return []
    lo = bisect.bisect_left(starts, now)
    hi = min(bisect.bisect_right(starts, now + PRECHARGE_LOOKAHEAD_S), len(events))
    out: List[object] = []
    for k in range(lo, hi):
        clip = _index_clip(events[k])
        # 该 clip 的触发点 trigger=start-lead 已到（含本帧余量 step）→ 该注册了。
        if clip is not None and _start_seconds(clip) - _lead_seconds(clip) <= now + step:
            out.append(clip)
    return out


def _index_clip(event) -> Optional[object]:
    """从播放索引条目 `(start, end, item)` 取 clip；结构异常时 None。"""
    try:
        return event[2][3]
    except (IndexError, TypeError):
        return None


def _active_clips(active_clip_items) -> List[object]:
    """活跃集条目 `(start, order, track, clip, ...)` → clip 列表。"""
    out: List[object] = []
    for item in active_clip_items or ():
        clip = item[3] if len(item) > 3 else None
        if clip is not None:
            out.append(clip)
    return out


def _frame_step_seconds(engine) -> float:
    try:
        return max(0.02, float(engine._timer.interval()) / 1000.0)
    except Exception:
        return 0.05


def _ensure_playback_index(engine, proj) -> None:
    """惰性确保播放索引可用（烘焙帧服务路径不经实时渲染循环，索引可能从未构建）。"""
    if isinstance(getattr(engine, "_playback_active_index", None), dict):
        return
    build = getattr(engine, "_playback_index_for_tracks", None)
    struct_of = getattr(engine, "_render_struct_for_tracks", None)
    tracks = list(getattr(proj, "tracks", []) or [])
    if not (build and struct_of and tracks):
        return
    try:
        build(tracks, struct_of(tracks)["ordered_tracks"])
    except Exception:
        log.debug("precharge: playback index build failed", exc_info=True)


def _needs_registration(scheduler, clip) -> bool:
    """有 clip_id 且未注册过（register 内部本就按 clip_id 幂等去重）。"""
    clip_id = str(getattr(clip, "id", "") or "")
    return bool(clip_id) and not scheduler.is_registered(clip_id)


def _register_candidates(scheduler, proj, clips) -> int:
    """逐个把候选 clip 的泵通道解析出来注册（幂等）。"""
    registered = 0
    for clip in clips:
        if not bool(getattr(clip, "precharge_enabled", False)):
            continue
        if not _needs_registration(scheduler, clip):
            continue
        pump_channels = find_pump_channels(clip, proj)
        if pump_channels:
            scheduler.register(clip, pump_channels)
            registered += 1
    return registered


def register_due_clips(engine, now: float, active_clip_items, proj) -> int:
    """触发到期事件 + 注册"该预充电"的 clip（R141 修复）。

    ★★ 修复前：注册只发生在 `_render_position` 的活跃集循环里，而活跃集的判据是
      `start_time <= now < start+duration` —— 预充电的价值区间恰好**在起点之前**
      （`[start-lead, start)`），那时 clip 根本不在活跃集里 → 事件从未被创建 →
      探针实测全程 `fired=0`、`send_raw` 一次都没调用（预充功能等于不存在，
      只有 seek 之后偶然落下那一帧才触发一次）。

    修法：活跃集内的 clip 照旧注册，另把 `start_time ∈ [now, now+前瞻窗口]`
      且触发点已到的 clip 一并纳入候选。`tick` 的触发判据一字未动 ——
      提前注册只是把事件准备好，真正发值仍严格发生在 `[trigger, start)` 内。

    Returns:
        本次新注册的 clip 数（诊断/测试用）。
    """
    scheduler = getattr(engine, "_precharge_scheduler", None)
    if scheduler is None or proj is None:
        return 0
    now = max(0.0, float(now))
    scheduler.tick(now)
    _ensure_playback_index(engine, proj)
    clips = _active_clips(active_clip_items)
    clips.extend(_lookahead_clips(engine, now, _frame_step_seconds(engine)))
    return _register_candidates(scheduler, proj, clips)


@dataclass
class PrechargeEvent:
    """一次预充电事件——记录需向某泵通道发送预充值的的时间与参数。"""
    clip_id: str
    universe: int
    channel: int           # 0-indexed DMX address
    level: int             # DMX value 0-255
    trigger_position: float  # 时间轴位置（秒），到达该时刻触发预充电
    clip_start: float      # 时间轴位置，clip 实际开始时刻（在此之后不再预充）
    fired: bool = False
    cancelled: bool = False


class PrechargeScheduler:
    """Pump precharge scheduler — fires DMX precharge values to pump channels
    before a clip starts, eliminating VFD startup delay.

    Usage:
        scheduler = PrechargeScheduler(network_manager)
        scheduler.register(clip, [(1, 10), (1, 11)])  # (universe, ch_1based)

        # In playback tick:
        scheduler.tick(current_position)

        # On clip stop/remove:
        scheduler.unregister(clip_id)

        # On playback stop:
        scheduler.reset()
    """

    def __init__(self, network_manager):
        self._nm = network_manager
        self._pending: List[PrechargeEvent] = []
        self._scheduled_clip_ids: Set[str] = set()

    # ── Public API ────────────────────────────────────────────────────────

    def register(self, clip: Clip, pump_channels: List[Tuple[int, int]]) -> None:
        """Register a precharge-enabled clip with its pump channels.

        Args:
            clip: The clip (must have precharge_enabled=True).
            pump_channels: List of (universe, channel_1based) tuples.
        """
        clip_id = str(getattr(clip, "id", "") or "")
        if not clip_id:
            return
        if not bool(getattr(clip, "precharge_enabled", False)):
            return
        if clip_id in self._scheduled_clip_ids:
            return  # Already registered

        # ★ R131：0 是合法提前量，不能用 `or` 兜底（否则 0 → 1000 且引擎侧不可达）
        _lead = getattr(clip, "precharge_lead_ms", 1000.0)
        lead_s = max(0.0, (1000.0 if _lead in (None, "") else float(_lead)) / 1000.0)
        trigger_position = max(0.0, float(clip.start_time) - lead_s)
        clip_start = float(clip.start_time)
        level = max(0, min(255, int(getattr(clip, "precharge_level", 13) or 0)))

        for universe, ch_1based in pump_channels:
            event = PrechargeEvent(
                clip_id=clip_id,
                universe=int(universe),
                channel=max(0, min(511, int(ch_1based) - 1)),
                level=level,
                trigger_position=trigger_position,
                clip_start=clip_start,
            )
            self._pending.append(event)

        self._scheduled_clip_ids.add(clip_id)
        log.debug(
            "Precharge registered: clip=%s, channels=%d, trigger=%.3fs, level=%d",
            clip_id, len(pump_channels), trigger_position, level,
        )

    def tick(self, current_position: float) -> None:
        """Check and fire due precharge events.

        Called from the playback engine's render tick before clip processing.
        Only fires when *current_position* is within the precharge window
        (trigger_position <= now < clip_start).

        Args:
            current_position: Current playback timeline position (seconds).
        """
        now = max(0.0, float(current_position))

        # ★ 性能：顺带修剪已失去触发可能的事件（触发窗口是 [trigger, clip_start)，
        #   已过 clip 起点的 fired 事件再无触发可能），防止播放整首歌后 _pending
        #   无限增长、每帧线性全扫。cancelled 事件按既有契约保留在列表（仅被跳过）。
        prune = []
        for event in self._pending:
            if event.fired or event.cancelled:
                if event.fired and now >= event.clip_start:
                    prune.append(event)
                continue
            if event.trigger_position <= now < event.clip_start:
                self._fire_event(event)
        if prune:
            for event in prune:
                try:
                    self._pending.remove(event)
                except ValueError:
                    pass

    def unregister(self, clip_id: str) -> None:
        """Cancel precharge for a specific clip (e.g. when clip is removed).

        Already-fired events are left in place (the clip's normal rendering
        will handle the channel values when active).
        """
        clip_id = str(clip_id or "")
        if not clip_id:
            return
        for event in self._pending:
            if event.clip_id == clip_id and not event.fired:
                event.cancelled = True
        self._scheduled_clip_ids.discard(clip_id)
        log.debug("Precharge unregistered: clip=%s", clip_id)

    def reset(self) -> None:
        """Clear all pending events. Called on playback stop."""
        self._pending.clear()
        self._scheduled_clip_ids.clear()

    # ── Internal ─────────────────────────────────────────────────────────

    def _fire_event(self, event: PrechargeEvent) -> None:
        """Send the precharge DMX value to the pump channel via send_raw."""
        if self._nm is None:
            log.warning("Precharge fire skipped: no network manager")
            return

        try:
            # Build a 512-byte frame preserving the current snapshot values
            data = np.zeros(512, dtype=np.uint8)
            snapshot = getattr(self._nm, "_universe_snapshot", {})
            if event.universe in snapshot:
                data[:] = snapshot[event.universe]
            data[event.channel] = event.level
            self._nm.send_raw(event.universe, data)
            event.fired = True
            log.debug(
                "Precharge fired: universe=%d ch=%d level=%d",
                event.universe, event.channel, event.level,
            )
        except Exception:
            log.exception("Precharge fire failed for universe=%d ch=%d",
                          event.universe, event.channel)

    # ── Query ────────────────────────────────────────────────────────────

    def is_registered(self, clip_id: str) -> bool:
        """该 clip 是否已注册预充电（引擎每帧据此跳过重复的全量扫描）。"""
        return str(clip_id or "") in self._scheduled_clip_ids

    @property
    def pending_count(self) -> int:
        """Number of pending (unfired, uncancelled) events."""
        return sum(1 for e in self._pending if not e.fired and not e.cancelled)

    @property
    def scheduled_clip_count(self) -> int:
        """Number of clips currently registered for precharge."""
        return len(self._scheduled_clip_ids)


# ── Helper: resolve pump channels from project fixtures ──────────────────

def find_pump_channels(clip: Clip, proj) -> List[Tuple[int, int]]:
    """Find pump-type DMX channels relevant to a clip from project fixtures.

    Iterates project fixtures, matches against fixture definitions to
    identify channels of type ``pump``, and checks against the clip's
    universe and active-channel scope.

    Args:
        clip: The clip to find pump channels for.
        proj: The project (must have ``fixtures`` and ``fixture_defs``).

    Returns:
        List of (universe, channel_1based) tuples for pump channels
        within the clip's scope.
    """
    pump_channels: List[Tuple[int, int]] = []

    if proj is None:
        return pump_channels

    # Universes the clip touches — fallback to [1]
    clip_universes: Set[int] = set(
        int(u) for u in (getattr(clip, "clip_universes", None) or [1])
    )

    # Active-channel map — None/empty means "all channels are active"
    active_map: Dict[int, List[int]] = (
        getattr(clip, "active_channels_by_universe", None) or {}
    )

    # Build fixture-def lookup table
    defs_by_id: Dict[str, object] = {
        str(getattr(fd, "id", "") or ""): fd
        for fd in (getattr(proj, "fixture_defs", None) or [])
        if getattr(fd, "id", None)
    }

    for fixture in getattr(proj, "fixtures", None) or []:
        universe = int(getattr(fixture, "universe", 1))
        if universe not in clip_universes:
            continue

        def_id = str(getattr(fixture, "def_id", "") or "")
        fixture_def = defs_by_id.get(def_id)
        if fixture_def is None:
            continue

        start_ch = int(getattr(fixture, "start_channel", 1))
        fixture_channels = list(getattr(fixture_def, "channels", None) or [])

        for ch_offset, ch_type in enumerate(fixture_channels):
            if ch_type != ChannelType.PUMP.value:
                continue

            dmx_addr = start_ch + ch_offset  # 1-based

            # If the clip has an active-channel list for this universe,
            # skip pump channels not in that list.
            if active_map:
                active_list = active_map.get(universe, [])
                if active_list and dmx_addr not in active_list:
                    continue

            pump_channels.append((universe, dmx_addr))

    return pump_channels
