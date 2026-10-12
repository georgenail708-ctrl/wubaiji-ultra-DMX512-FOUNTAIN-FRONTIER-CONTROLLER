"""
Qascade V1.0 — FrameBufferPool
Pre-allocated pool of 512-byte DMX frame buffers for zero-allocation
copies on the hot render path.

The pool uses a circular-buffer approach: each call to ``copy_from()``
returns the next pre-allocated buffer with the source data copied in.
At the start of a frame tick the cursor is reset (``reset()``), so the
same internal buffers are reused every tick.

**Contract**: Do **not** store a buffer returned by ``copy_from()`` across
frame-tick boundaries — the backing memory will be overwritten on the
next tick's ``copy_from()`` calls.  For data that must persist (caches,
recording snapshots) use a regular ``.copy()`` instead.

Thread-safe (``Lock`` guards the cursor and the pool list).
"""

from __future__ import annotations

import threading
from typing import List, Optional

import numpy as np


class FrameBufferPool:
    """Pre-allocated pool of 512-byte DMX frame buffers.

    Provides zero-allocation ``copy_from()`` for the hot render/output
    path.  Call ``reset()`` at the beginning of each frame tick to recycle
    all slots.

    Parameters
    ----------
    pool_size : int
        Number of pre-allocated buffers.  Must be >= the maximum number
        of buffer copies taken in a single frame tick.  Default 256 is
        safe for worst-case renders (many clips x many universes).
    """

    FRAME_SIZE = 512
    # ★ 性能：11382 台/146+ clip 工程单 tick 可申请 900-1700 次缓冲
    #   （远超 256），耗尽会退回堆分配抵消池化收益；2048 覆盖最坏规模。
    DEFAULT_POOL_SIZE = 2048

    def __init__(self, pool_size: int = DEFAULT_POOL_SIZE):
        if pool_size < 1:
            raise ValueError("pool_size must be >= 1")
        self._pool: List[np.ndarray] = [
            np.zeros(self.FRAME_SIZE, dtype=np.uint8) for _ in range(pool_size)
        ]
        self._cursor: int = 0
        self._lock = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────

    def copy_from(self, source: np.ndarray) -> np.ndarray:
        """Zero-allocation copy of *source* into a pre-allocated buffer.

        Falls back to ``source.copy()`` if the pool is exhausted this tick
        (i.e. more copies were requested than ``pool_size``).
        """
        with self._lock:
            if self._cursor < len(self._pool):
                buf = self._pool[self._cursor]
                self._cursor += 1
            else:
                # Pool exhausted this tick — fall back to regular allocation
                return source.copy()
        np.copyto(buf, source)
        return buf

    def reset(self) -> None:
        """Recycle all buffers.  Call at the start of each frame tick.

        After this call, subsequent ``copy_from()`` invocations will
        reuse previously returned buffers.
        """
        with self._lock:
            self._cursor = 0

    # ── Properties ─────────────────────────────────────────────────

    @property
    def capacity(self) -> int:
        """Total number of pre-allocated buffers."""
        return len(self._pool)

    @property
    def used_this_tick(self) -> int:
        """Number of buffers handed out since the last ``reset()``."""
        with self._lock:
            return self._cursor

    @property
    def remaining_this_tick(self) -> int:
        """Number of buffers still available this tick before fallback."""
        with self._lock:
            return max(0, len(self._pool) - self._cursor)

    def __repr__(self) -> str:
        return (
            f"<FrameBufferPool capacity={self.capacity} "
            f"used={self.used_this_tick}>"
        )
