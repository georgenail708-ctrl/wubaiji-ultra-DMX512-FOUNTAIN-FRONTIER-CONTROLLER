"""完整时间线帧的有界存储：小工程 dict，大工程直接复用 DiskCache。

帧/掩码不压损、不逐出。普通磁盘操作仍关闭本线程连接；原owned烘焙作用域
内的新store复用writer连接，在同线程finally关闭后才交接，避免Windows库锁。
参考 https://github.com/grantjenks/python-diskcache/blob/master/diskcache/core.py
"""
from __future__ import annotations

from collections.abc import MutableMapping
from tempfile import TemporaryDirectory
from threading import RLock
from types import MappingProxyType
import sys

from diskcache import Cache
import numpy as np

from .timeline_frame_writer import (
    after_store_clear, cache_context, check_clear_owner, close_cache_connection, register_store,
)

MEMORY_BUDGET = 32 * 1024 * 1024  # 播放帧/录制源各32MiB，独立预算。
READ_WINDOW_BYTES = 2 * 1024 * 1024
READ_WINDOW_FRAMES = 16  # 限一次同步加载成本；始终低于64项上限。
_MISSING = object()


def _frame_size(frame):
    """计入数组实体、tuple、dict 以及帧索引开销（引擎已复制连续数组）。"""
    return sys.getsizeof(frame) + 96 + sum(
        sys.getsizeof(key) + sys.getsizeof(pair)
        + sum(sys.getsizeof(array) for array in pair)
        for key, pair in frame.items()
    )


def _freeze_read_frame(frame):
    """读取窗基底不可回写；实际计入 ndarray 背后的 bytes 实体。"""
    frozen = {
        key: tuple(np.ndarray(shape=array.shape, dtype=array.dtype, buffer=array.tobytes())
                   for array in pair)
        for key, pair in frame.items()
    }
    size = _frame_size(frozen) + sum(
        array.nbytes + sys.getsizeof(b"") for pair in frozen.values() for array in pair)
    return MappingProxyType(frozen), size + sys.getsizeof(MappingProxyType({}))


class TimelineFrameStore(MutableMapping):
    """兼容现有 keys/items/get/clear 的完整帧映射；磁盘数据不设过期。"""

    def __init__(self, memory_budget=MEMORY_BUDGET):
        self.memory_budget = max(0, int(memory_budget))
        self.memory_bytes = 0
        self._memory = {}
        self._disk = None
        self._temporary = None
        self._count = 0
        self._lock = RLock()
        self._read_window = {}
        self._read_window_bytes = 0
        self._writer_owner = None
        if type(self) is TimelineFrameStore:
            register_store(self)

    @property
    def spilled(self):
        return self._disk is not None

    @property
    def directory(self):
        return self._temporary.name if self._temporary is not None else None

    def _spill(self):
        """迁移成功才释放内存帧；磁盘失败留下原表并向烘焙所有者报错。"""
        temporary = TemporaryDirectory(prefix="qascade_bake_")
        try:
            with Cache(temporary.name, eviction_policy="none", cull_limit=0,
                       disk_min_file_size=1024 ** 3, sqlite_mmap_size=0,
                       sqlite_cache_size=-1024) as cache:
                with cache.transact():
                    for key, value in self._memory.items():
                        cache[key] = value
        except BaseException:
            temporary.cleanup()
            raise
        self._disk = cache
        self._temporary = temporary
        self._count = len(self._memory)
        self._memory.clear()
        self.memory_bytes = 0

    def __setitem__(self, key, value):
        with self._lock:
            self._clear_read_window()
            if self._disk is None:
                old_size = _frame_size(self._memory[key]) if key in self._memory else 0
                new_size = self.memory_bytes - old_size + _frame_size(value)
                if new_size <= self.memory_budget:
                    self._memory[key] = value
                    self.memory_bytes = new_size
                    return
                self._spill()
            with cache_context(self) as cache:
                existed = key in cache
                cache[key] = value
                self._count += int(not existed)

    def __getitem__(self, key):
        with self._lock:
            if self._disk is None:
                return self._memory[key]
            with cache_context(self) as cache:
                return cache[key]

    def read_frame(self, key, default=None):
        """播放只读消费；批量短连接读取，不改变原可写 mapping API。"""
        with self._lock:
            if self._disk is None:
                return self._memory.get(key, default)
            value = self._read_window.get(key, _MISSING)
            if value is _MISSING:
                value = self._fill_read_window(key)
            return default if value is _MISSING else value

    def _clear_read_window(self):
        self._read_window.clear()
        self._read_window_bytes = 0

    def _fill_read_window(self, key):
        self._clear_read_window()
        with cache_context(self) as cache:
            if not isinstance(key, int):
                return cache.get(key, _MISSING, retry=True)
            requested = _MISSING
            for index in range(key, key + min(64, READ_WINDOW_FRAMES)):
                raw = cache.get(index, _MISSING, retry=True)
                if raw is _MISSING:
                    break
                value, size = _freeze_read_frame(raw)
                if index == key:
                    requested = value
                if self._read_window_bytes + size > READ_WINDOW_BYTES:
                    break
                self._read_window[index] = value
                self._read_window_bytes += size
        return requested

    def __delitem__(self, key):
        with self._lock:
            self._clear_read_window()
            if self._disk is None:
                self.memory_bytes -= _frame_size(self._memory[key])
                del self._memory[key]
                return
            with cache_context(self) as cache:
                del cache[key]
                self._count -= 1

    def __contains__(self, key):
        with self._lock:
            if self._disk is None:
                return key in self._memory
            with cache_context(self) as cache:
                return key in cache

    def __len__(self):
        with self._lock:
            return len(self._memory) if self._disk is None else self._count

    def __iter__(self):
        with self._lock:
            if self._disk is None:
                return iter(tuple(self._memory))
            with cache_context(self) as cache:
                return iter(tuple(cache.iterkeys()))

    def clear(self):
        """只清理由本实例 TemporaryDirectory 创建的私有缓存目录。"""
        with self._lock:
            check_clear_owner(self)
            if self._disk is not None:
                close_cache_connection(self._disk)
            if self._temporary is not None:
                self._temporary.cleanup()
            after_store_clear(self)
            self._clear_read_window()
            self._memory.clear()
            self.memory_bytes = 0
            self._count = 0
            self._disk = None
            self._temporary = None
