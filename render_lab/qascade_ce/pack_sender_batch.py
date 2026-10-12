"""PACK with original sender sequence and locking semantics (R185 closeout).

Original packetizers and C ABI are reused. No separate counters or queues.
Array preparation precedes locks; sequence reservation and delivery share locks.
"""
from contextlib import ExitStack
import ctypes
import logging

import numpy as np

from src.network import sacn_node

log = logging.getLogger(__name__)
_U8 = ctypes.POINTER(ctypes.c_uint8)
_U16 = ctypes.POINTER(ctypes.c_uint16)
_I32 = ctypes.POINTER(ctypes.c_int32)


def _arrays(universes, data):
    n = len(data)
    unis = np.asarray(universes, dtype=np.uint16)
    lens = np.full(n, 512, np.int32)
    frame = np.zeros((n, 512), np.uint8)
    for i, row in enumerate(data):
        frame[i] = row
    return unis, lens, frame


def _pack(kernel, arrays, sequences, priorities=None):
    unis, lens, frame = arrays
    n = len(unis)
    size = 530 if priorities is None else 638
    out = np.zeros(n * size, np.uint8)
    offsets = np.empty(n, np.int32)
    seqs = np.asarray(sequences, dtype=np.uint8)
    args = (unis.ctypes.data_as(_U16), seqs.ctypes.data_as(_U8))
    if priorities is not None:
        cid = (ctypes.c_ubyte * 16).from_buffer_copy(sacn_node._SOURCE_CID)
        name = (ctypes.c_ubyte * 64).from_buffer_copy(sacn_node._SOURCE_NAME)
        pris = np.asarray(priorities, dtype=np.uint8)
        args = (cid, name, *args, pris.ctypes.data_as(_U8))
    total = kernel(n, *args, lens.ctypes.data_as(_I32), frame.ctypes.data_as(_U8),
                   out.ctypes.data_as(_U8), offsets.ctypes.data_as(_I32))
    if total != n * size or not np.array_equal(offsets, np.arange(n) * size):
        raise ValueError("PACK returned an invalid packet layout")
    return [bytes(out[i * size:(i + 1) * size]) for i in range(n)]


def _restore_art_sequences(sender, previous):
    for universe, value in previous.items():
        if value is None:
            sender._seqs.pop(universe, None)
        else:
            sender._seqs[universe] = value


def _art_locked(sender, items, arrays, kernel):
    with sender._send_lock:
        previous = {u: sender._seqs.get(u) for u, _ in items}
        try:
            sequences = [sender._next_seq(u) for u, _ in items]
            packets = _pack(kernel, arrays, sequences)
        except Exception:
            _restore_art_sequences(sender, previous)
            raise
        for (universe, _), packet in zip(items, packets):
            sender._packets += 1
            sender._sendto_all(packet, universe)


def send_artnet_batch(sender, frames, kernel):
    items = [(int(u), np.asarray(d, dtype=np.uint8)) for u, d in frames.items()]
    if not items:
        return True
    if len(items) == 1 or kernel is None or any(d.size != 512 for _, d in items):
        for universe, data in items:
            sender.send_dmx(universe, data)
        return True
    try:
        arrays = _arrays([u & 0xFFFF for u, _ in items], [d for _, d in items])
        _art_locked(sender, items, arrays, kernel)
    except Exception:
        for universe, data in items:
            sender.send_dmx(universe, data)
    return True


def _sacn_items(pool, frames):
    items = []
    for universe, data in frames.items():
        sender = pool.get(int(universe))
        arr = np.asarray(data, dtype=np.uint8)
        if sender is None or arr.size != 512:
            return None
        items.append((sender, arr))
    return items


def _send_sacn_packet(sender, packet):
    sender._seq = (sender._seq + 1) % 256
    sender._packets += 1
    try:
        sender._sock.sendto(packet, (sender.target_ip, sacn_node.SACN_PORT))
    except Exception as error:
        sender._errors += 1
        log.debug("[sACN] Send error (universe=%s): %s", sender.universe, error)


def _sacn_locked(items, arrays, kernel):
    owners = {id(sender): sender for sender, _ in items}
    ordered = sorted(owners.values(), key=lambda sender: (sender.universe, id(sender)))
    with ExitStack() as stack:
        for sender in ordered:
            stack.enter_context(sender._send_lock)
        packets = _pack(kernel, arrays, [sender._seq for sender, _ in items],
                        [sender.priority for sender, _ in items])
        for (sender, _), packet in zip(items, packets):
            _send_sacn_packet(sender, packet)


def send_sacn_batch(pool, frames, kernel):
    items = _sacn_items(pool, frames)
    if items is None:
        return False  # Original NM fallback creates missing senders.
    if not items:
        return True
    if len(items) == 1 or kernel is None:
        for sender, data in items:
            sender.send_dmx(sender.universe, data)
        return True
    try:
        arrays = _arrays([s.universe & 0xFFFF for s, _ in items], [d for _, d in items])
        _sacn_locked(items, arrays, kernel)
    except Exception:
        for sender, data in items:
            sender.send_dmx(sender.universe, data)
    return True
