"""Actual store/writer/DiskCache behavior controls with bounded IO."""
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from src.engine.timeline_frame_store import TimelineFrameStore
from src.engine.timeline_frame_writer import owned_frame_writer_scope


def frame(value):
    data = np.full(512, value, dtype=np.uint8)
    active = np.zeros(512, dtype=np.bool_)
    active[17] = True
    return {3: (data, active)}


def test_memory_batch_preserves_original_mutable_frame_aliases():
    with owned_frame_writer_scope():
        store = TimelineFrameStore()
        first, second = frame(0), frame(9)
        store.write_batch(((4, first), (5, second)))
        assert store[4] is first and store[5] is second
        first[3][0][17] = 12
        assert store[4][3][0][17] == 12
        assert store[4][3][1][17] and len(store) == 2
        assert not store.spilled
    store.clear()


def test_disk_batch_overwrite_count_values_and_readonly_window():
    with owned_frame_writer_scope():
        store = TimelineFrameStore(memory_budget=0)
        source = frame(0)
        store.write_batch(((4, source), (5, frame(9)), (4, frame(7))))
        source[3][0][17] = 99
        assert len(store) == 2 and store.spilled
        assert np.all(store[4][3][0] == 7)
        assert np.all(store[5][3][0] == 9)
        cached = store.read_frame(4)
        assert cached[3][1][17] and not cached[3][0].flags.writeable
        with pytest.raises(ValueError):
            cached[3][0][17] = 30
    store.clear()


def test_disk_transaction_failure_restores_previous_values_and_count():
    with owned_frame_writer_scope():
        store = TimelineFrameStore(memory_budget=0)
        store[4] = frame(7)

        def interrupted():
            yield 4, frame(1)
            yield 5, frame(2)
            raise RuntimeError('completed source retired')

        with pytest.raises(RuntimeError, match='completed source retired'):
            store.write_batch(interrupted())
        assert len(store) == 1 and 5 not in store
        assert np.all(store[4][3][0] == 7)
        assert store.read_frame(4)[3][1][17]
    store.clear()


def test_unowned_and_synthetic_writer_are_rejected_before_consuming_input():
    store = TimelineFrameStore()
    consumed = []

    def values():
        consumed.append(True)
        yield 4, frame(7)

    with pytest.raises(RuntimeError, match='actual current registered writer'):
        store.write_batch(values())
    store._writer_owner = SimpleNamespace(is_current=lambda: True)
    try:
        with pytest.raises(RuntimeError, match='actual current registered writer'):
            store.write_batch(values())
    finally:
        store._writer_owner = None
    assert not consumed and len(store) == 0
    store.clear()


def test_actual_writer_closes_connection_before_private_directory_cleanup():
    with owned_frame_writer_scope():
        store = TimelineFrameStore(memory_budget=0)
        store.write_batch(((4, frame(0)), (5, frame(9))))
        cache, directory = store._disk, Path(store.directory)
        assert directory.exists() and cache._local.con is not None
        connection = cache._local.con
    assert store._writer_owner is None and getattr(cache._local, "con", None) is None
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
    store.clear()
    assert not directory.exists()
