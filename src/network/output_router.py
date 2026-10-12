"""
Qascade V1.0 — OutputRouter
独立输出路由线程 — 从 gen 线程接收编译好的 DMX 帧，在独立线程中发送 socket I/O。

将 socket I/O 从 gen 线程分离，减少 GIL 持锁时间。

★ v3 (2026-08-04) 发送链优化：
  - 存储从 collections.deque 改为 dict（{universe: data|None}）：
      push 由 O(队列长度) 扫描 → O(1) 键写，同 universe 覆盖保持插入序
      （dict 值替换不改变键位置），latest-wins 语义与旧 deque 逐点一致；
  - 消费者批量出队：一次持锁取空全部待发帧，锁外逐包发送
      （旧实现每包一次 lock 获取 + popleft，73 域/tick → 73 次锁）；
  - maxlen=512 语义保留：字典长度 ≥ 512 且新键时丢最旧条目
      （next(iter(...)) O(1) 取最旧）。

Usage::

    router = OutputRouter(artnet_sender)
    router.start()
    router.push(1, dmx_frame)
    router.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional, Union

import numpy as np

from .output_sink import IOutputSink
from .output_record import OutputIdentity, OutputRecord
from .output_attempt_scope import invoke_attempt
from .output_pacing import BatchPacer
from .output_batch_delivery import deliver_dequeued

log = logging.getLogger(__name__)

# Type alias: dispatch target may be an IOutputSink or a raw callable
_Sink = Union[IOutputSink, Callable[[int, np.ndarray], None]]
_RecordSink = Callable[[OutputRecord], None]


def _coerce_frame(data) -> np.ndarray:
    """将任意数值帧钳制为独立 uint8 数组；非法输入由调用方捕获。"""
    raw = np.asarray(data)
    if raw.dtype.kind not in "biuf":
        raise TypeError("DMX frame must contain numeric values")
    if raw.dtype == np.uint8:
        return raw.copy()
    return np.clip(raw, 0, 255).astype(np.uint8, copy=True)


def _source_record(universe: int, data: np.ndarray, identity=None) -> Optional[OutputRecord]:
    """Optional borrowed source metadata; never changes original send validation.

    Keeps packet and serial together (semantics
    only, LGPL-2.1+; no copied code). Adoption: r185-output-record-adoption.md §7.
    """
    origin = identity if type(identity) is OutputIdentity else None
    try:
        return OutputRecord(universe_1based=universe, data=data, identity=origin)
    except Exception:
        # Original router does not validate protocol domains; optional schema
        # failures must not change its coercion, queue or two-argument send.
        return None


class OutputRouter:
    """Independent output-routing thread.

    Receives already-mixed DMX frames on a lightweight queue (gen thread)
    and sends them through the actual I/O sink on a dedicated daemon thread,
    so socket I/O never blocks the mixer or holds the GIL for long.

    Backpressure: ``maxlen=512``(Art-Net 理论 universe 上限)。容量
    必须大于单 tick 推送量(大工程 73 个 universe / tick 可达 19~73 帧),
    否则丢旧条目会结构性丢帧——丢的恰好是最先入队的低编号 universe
    (底弧线左侧设备),表现为"左侧一小部分设备延迟响应"。同 universe 的
    旧帧被新帧覆盖(per-universe latest-wins),每个 universe 至多占一条,
    排队延迟有界。
    """

    def __init__(self, sink: _Sink, keepalive_interval: float = 1.0, *,
                 record_sink: Optional[_RecordSink] = None,
                 attempt_scope: Optional[Callable] = None) -> None:
        self._sink: _Sink = sink
        self._record_sink = record_sink
        self._attempt_scope = attempt_scope
        # 512 = Art-Net 理论上限 universe 数,保证稳态(每域至多一条)
        # 永不触发丢最旧。dict: universe -> np.ndarray | None (心跳占位)。
        # 键替换不改变插入序 → 发送序 = 各 universe 首次入队序(与旧 deque 一致)。
        self._pending: Dict[int, Optional[np.ndarray]] = {}
        self._maxlen = 512
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        # R178 keepalive：变化域检测跳过的域靠 None 心跳注入节拍，
        # 这里按 keepalive_interval（默认 1s）把"上一帧"原样重发，
        # 防接收端（灯具/可视化器 ~2.5s 无包超时）掉帧。
        self._keepalive_interval = max(0.05, float(keepalive_interval))
        # Private zero remains an explicit forced deadline for legacy witnesses.
        # Normal scheduling is relative to each universe's last sink attempt.
        self._last_keepalive_t = time.monotonic()
        self._last_frames: Dict[int, np.ndarray] = {}
        self._last_attempt_t: Dict[int, float] = {}
        self._pending_queued_at: Dict[int, float] = {}
        self._delivery_stats = {"submitted": 0, "replaced": 0, "evicted": 0,
                                "attempted": 0, "queue_age_max_ms": 0.0}
        if record_sink is not None:
            self._pending_records: Dict[int, OutputRecord] = {}
            self._last_records: Dict[int, OutputRecord] = {}

    def configure_observation(self, *, record_sink=None, attempt_scope=None):
        """Attach optional metadata without restarting or discarding output.

        Existing queued frames have no new source identity. Enabling starts
        empty metadata; only a subsequent producer submission can qualify.
        """
        with self._lock:
            self._record_sink = None
            if record_sink is not None:
                self._pending_records = {}
                self._last_records = {}
            self._attempt_scope = attempt_scope
            self._record_sink = record_sink

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the router daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="output-router",
        )
        self._thread.start()
        log.info("OutputRouter 已启动")

    def stop(self) -> None:
        """Signal the router thread to stop and wait for it (timeout 2s)."""
        self._running = False
        self._event.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                log.warning("OutputRouter 线程未能在 2s 内停止")
        self._thread = None
        with self._lock:
            self._pending.clear()
            self._last_frames.clear()
            self._last_attempt_t.clear()
            self._pending_queued_at.clear()
            if self._record_sink is not None:
                self._pending_records.clear()
                self._last_records.clear()
        log.info("OutputRouter 已停止")

    @property
    def is_alive(self) -> bool:
        """Whether the router thread is currently running."""
        return self._thread is not None and self._thread.is_alive()

    # ── producer interface (gen thread) ──────────────────────────────────────

    def push(self, universe: int, data: np.ndarray, *,
             identity: Optional[OutputIdentity] = None) -> None:
        """Enqueue a DMX frame for output (gen thread, fast path).

        ``data`` is **copied** at push time so the generator and router
        threads never share a mutable numpy array.

        Per-universe latest-wins: 若该 universe 已有未发送条目(含心跳占位),
        原地覆盖为新帧而非追加——大工程单 tick 推送 19~73 帧远超旧 maxlen=8,
        丢旧条目会反复丢弃最先入队的低编号 universe(底弧线左侧设备),
        表现为左侧设备延迟响应。覆盖保证每个 universe 至多占一条,排队有界。
        """
        uni = int(universe)
        # 统一为独立的 uint8 数组，边界值钳制后再复制，避免异常数据让输出线程崩溃。
        copied = _coerce_frame(data)
        record = (_source_record(uni, copied, identity)
                  if self._record_sink is not None else None)
        with self._lock:
            self._store_pending(uni, copied, record)
        self._event.set()

    def push_batch(self, frames: dict, *, identities: Optional[dict] = None) -> None:
        """Enqueue several DMX frames at once.

        ``frames`` is ``{universe: np.ndarray}``.  Each frame is copied.
        Same per-universe latest-wins semantics as :meth:`push`.
        """
        # 在锁外先完成复制/类型转换，缩短持锁时间；消费线程可更早开始 drain。
        prepared = {}
        records = {} if self._record_sink is not None else None
        for universe, data in frames.items():
            uni = int(universe)
            identity = (identities.get(uni)
                        if records is not None and isinstance(identities, dict) else None)
            copied = _coerce_frame(data)
            prepared[uni] = copied
            if records is not None:
                records[uni] = _source_record(uni, copied, identity)
        with self._lock:
            for uni, copied in prepared.items():
                self._store_pending(uni, copied, records.get(uni) if records is not None else None)
        self._event.set()

    def _store_pending(self, universe: int, data: np.ndarray,
                       record: Optional[OutputRecord]) -> None:
        """Pair with the original dict under its lock; retain latest-wins order."""
        if universe not in self._pending and len(self._pending) >= self._maxlen:
            oldest = next(iter(self._pending))
            discarded = self._pending.pop(oldest)
            self._pending_queued_at.pop(oldest, None)
            self._delivery_stats["evicted"] += int(discarded is not None)
            if self._record_sink is not None:
                self._pending_records.pop(oldest, None)
        self._delivery_stats["submitted"] += 1
        self._delivery_stats["replaced"] += int(self._pending.get(universe) is not None)
        self._pending[universe] = data
        self._pending_queued_at[universe] = time.monotonic()
        if self._record_sink is not None:
            if record is None:
                self._pending_records.pop(universe, None)
            else:
                self._pending_records[universe] = record

    def send_heartbeat(self, universes: list) -> None:
        """Push heartbeat markers (keepalive beats) for the given universe list.

        ★ R178: The router thread treats ``None``-data entries as a
        keep-alive beat: the beat triggers :meth:`_maybe_keepalive`, which
        resends each universe's *last frame* (byte-identical) at most once
        per ``keepalive_interval`` (default 1.0s). 变化域检测跳过的域靠
        这里维持接收端的"在场"（防 ~2.5s 无包超时掉帧）。

        Heartbeats never displace a pending real frame: if the universe
        already has an entry (data or marker), the marker is skipped, and
        a full queue only drops markers, never real frames.
        """
        with self._lock:
            for u in universes:
                uni = int(u)
                if uni in self._pending:
                    continue
                if len(self._pending) >= self._maxlen:
                    # 保活标记是可丢弃的；绝不能挤掉尚未发送的真实帧。
                    marker = next((key for key, value in self._pending.items()
                                   if value is None), None)
                    if marker is None:
                        continue
                    self._pending.pop(marker, None)
                    if self._record_sink is not None:
                        self._pending_records.pop(marker, None)
                self._pending[uni] = None
                if self._record_sink is not None:
                    self._pending_records.pop(uni, None)
        self._event.set()

    # ── consumer (router thread) ─────────────────────────────────────────────

    def _run(self) -> None:
        """Router thread main loop: drain pending frames and send.

        Runs at the thread's own pace — socket errors are caught and logged
        without crashing the generator.

        ★ v3: 批量出队 — 一次持锁取空全部待发帧(锁外发送)。pop 与 push
        共用 _lock,但持锁区间只剩 dict 搬运。
        ★ R178: None 心跳 = keepalive 节拍（1s 重发上一帧,字节不变）。
        ★ A10 G4: 保活与混音产出**解耦**——空闲 wait 带 keepalive 超时,
        到期自发 `_maybe_keepalive`（对照 artnetcontroller.cpp:28
        SEND_INTERVAL_MS=2000 的独立 m_sendTimer→slotSendAllUniverses：
        保活不该依赖"当拍混音有没有产出标记"。修前纯静态场景（全部域
        未变化）没有任何 None 标记入队 → keepalive 永不触发 → 接收端
        ~2.5s 无包超时掉帧）。`_last_frames` 语义不变。
        """
        while self._running:
            drained = self._drain_once()
            # A continuously busy dynamic universe must not starve static ones.
            if self._running:
                self._maybe_keepalive()
            if not drained and self._running:
                # 空闲：push/push_batch/send_heartbeat/stop 置位 event 提前唤醒；
                # 否则最多睡一个 keepalive 间隔，醒来先看停机再看保活。
                self._event.wait(self._keepalive_wait())
                self._event.clear()
                if self._running:
                    self._maybe_keepalive()

    def _drain_once(self) -> bool:
        """出队一批并发送（_run 单拍；空批返回 False 供事件等待）。"""
        with self._lock:
            if not self._pending:
                return False
            items = list(self._pending.items())
            records = None
            if self._record_sink is not None:
                records = self._pending_records.copy()
                self._pending_records.clear()
            self._pending.clear()
            queued_at = self._pending_queued_at.copy()
            self._pending_queued_at.clear()
        frames = [(u, data) for u, data in items if data is not None]
        deliver_dequeued(self, frames, records, queued_at, BatchPacer)
        # Only after fresh real frames: a marker cannot replay a dequeued old value.
        if len(frames) != len(items):
            self._maybe_keepalive()
        return True

    def _send_one(self, universe: int, data: np.ndarray, *,
                  record: Optional[OutputRecord] = None) -> None:
        """实发一帧并记录为该域"上一帧"（keepalive 重发源；线程内）。"""
        self._last_frames[universe] = data
        self._delivery_stats["attempted"] += 1
        try:
            if self._attempt_scope is None:
                self._send_and_notify(universe, data, record)
            else:
                invoke_attempt(lambda: self._send_and_notify(universe, data, record),
                               self._attempt_scope, record=record,
                               universe_1based=universe, data=data)
        finally:
            # Errors remain eligible for a bounded retry; this is not socket success.
            self._last_attempt_t[universe] = time.monotonic()

    def _send_and_notify(self, universe: int, data: np.ndarray,
                         record: Optional[OutputRecord]) -> None:
        """Original two-argument sink followed by the original source notice."""
        try:
            if isinstance(self._sink, IOutputSink):
                self._sink.send_dmx(universe, data)
            else:
                self._sink(universe, data)
        except Exception:
            log.exception("OutputRouter 发送失败 (universe=%s)", universe)
        if self._record_sink is not None:
            self._notify_record(universe, data, record)

    def _notify_record(self, universe: int, data: np.ndarray,
                       record: Optional[OutputRecord]) -> None:
        """Post-attempt source notification, not final bytes or socket success."""
        if (type(record) is not OutputRecord or record.data is not data
                or record.universe_1based != universe):
            # Legacy direct sends and bypassed/replaced last arrays stay unknown.
            record = _source_record(universe, data)
        if record is None:
            self._last_records.pop(universe, None)
            return
        self._last_records[universe] = record
        try:
            self._record_sink(record)
        except Exception:
            log.exception("OutputRouter 来源通知失败 (universe=%s)", universe)

    def _maybe_keepalive(self) -> None:
        """逐域尝试截止时间保活，动态域不额外重发；静态域字节不变。

        ``_last_keepalive_t=0`` 保留私有强制截止时间的见证合同；
        时钟回退使旧截止时间失效，未知旧帧也先做一次有界尝试。
        新真帧仍在 pending 时跳过该域，避免保活倒退到它的旧值。
        """
        now = time.monotonic()
        previous_beat = self._last_keepalive_t
        forced = previous_beat == 0.0 or now < previous_beat
        self._last_keepalive_t = now
        due = [(u, frame) for u, frame in list(self._last_frames.items())
               if forced or now - self._last_attempt_t.get(u, 0.0) >= self._keepalive_interval]
        pacer = BatchPacer(len(due))
        for index, (universe, frame) in enumerate(due):
            pacer.wait(index)
            with self._lock:
                pending_real = self._pending.get(universe) is not None
            if pending_real:
                continue
            if self._record_sink is None:
                self._send_one(universe, frame)
            else:
                self._send_one(universe, frame, record=self._last_records.get(universe))

    def _keepalive_wait(self) -> float:
        """Idle wait ends at the nearest per-universe deadline, never a new cycle."""
        if not self._last_frames:
            return self._keepalive_interval
        deadline = min(self._last_attempt_t.get(u, 0.0) + self._keepalive_interval
                       for u in self._last_frames)
        return max(0.0, min(self._keepalive_interval, deadline - time.monotonic()))

    def get_delivery_stats(self) -> dict:
        """Real-frame queue counts and all sink attempts; no receiver-delivery claim."""
        with self._lock:
            return dict(self._delivery_stats)
