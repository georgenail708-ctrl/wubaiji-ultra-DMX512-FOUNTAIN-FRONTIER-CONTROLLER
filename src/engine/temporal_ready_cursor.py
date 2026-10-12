"""One closed causal journal reader; adjacent entries reuse its unique outlet."""
import time

from .temporal_journal import (read_complete_temporal, unpack_payload,
                               apply_complete_entry, _record_read_cost)


def temporal_at(record, cursor, index):
    if cursor is None or index != cursor[0] + 1:
        return read_complete_temporal(record, index)
    from .temporal_journal import DOMAINS, BLOCK_FRAMES
    if (record.get("schema") != 1 or record.get("causal_anchor_schema") != 1
            or record.get("domains") != DOMAINS or record.get("block") != BLOCK_FRAMES):
        raise ValueError("noncurrent complete temporal cursor schema")
    store = record["store"]
    if store._writer_owner is not None:
        raise RuntimeError("a live temporal cursor cannot borrow owned SQL writer")
    if not -1 <= index <= record["last"]:
        raise KeyError(index)
    started, copies = time.perf_counter(), {"mapping_copies": 0}
    try:
        entry = unpack_payload(store.read_frame(index))
        return apply_complete_entry(cursor[1], entry, expected_index=index, counter=copies,
                                    source_token=record.get("causal_source_token"))
    finally:
        _record_read_cost(record, started, copies)
