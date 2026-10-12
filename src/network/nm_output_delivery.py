"""Thin ordinary NM source/actual-call composition; no output engine or queue.

Reuses the original Router, borrowed OutputRecord and NMFinalOutputTap.
CPython ContextVar adoption.
None performs only original calls; optional pairing failures never retry them.
"""
from __future__ import annotations

from .output_record import OutputRecord


def capture_nm_sources(nm, packets):
    """Caller owns the original NM state lock, including the computed mix."""
    tap = nm._final_output_tap
    if tap is None:
        return None
    try:
        return tap.capture_sources(packets)
    except Exception:
        return None


def invoke_nm_batch(nm, packets, records):
    tap = nm._final_output_tap
    if tap is None:
        return nm._send_batch_to_hw(packets)
    return tap.invoke_batch(nm._send_batch_to_hw, packets, records)


def read_source_identity(nm, universe, data):
    tap = nm._final_output_tap
    if tap is None:
        return None
    try:
        return tap.source_identity(universe, data)
    except Exception:
        return None


def accept_single_source(nm, universe, data):
    tap = nm._final_output_tap
    if tap is None:
        return nm._send_to_hw(universe, data)
    records = capture_nm_sources(nm, ((universe, data),))
    if records:
        return tap.invoke_source(nm._send_to_hw, records[0])
    return nm._send_to_hw(universe, data)


def create_nm_output_router(nm, router_type):
    tap = nm._final_output_tap
    if tap is None:
        return router_type(nm._direct_send_dmx)
    return router_type(nm._direct_send_dmx, record_sink=tap.after_record,
                       attempt_scope=tap.attempt_scope)


def push_changed_sources(nm, router, changed):
    if nm._final_output_tap is None:
        return router.push_batch(changed)
    identities = {}
    for universe, data in changed.items():
        identity = read_source_identity(nm, universe, data)
        if identity is not None:
            identities[universe] = identity
    return router.push_batch(changed, identities=identities)


def route_single_source(nm, universe, data, identity):
    router = nm._output_router
    if nm._use_output_router and router is not None and router.is_alive:
        if nm._final_output_tap is None:
            router.push(universe, data)
        else:
            router.push(universe, data, identity=identity)
        return
    nm._send_direct_frame(int(universe), data)


def _send_prepared_frame(nm, universe, data):
    identity = read_source_identity(nm, universe, data)
    if identity is None:
        return nm._send_direct_frame(universe, data)
    try:
        record = OutputRecord(universe_1based=universe, data=data, identity=identity)
    except Exception:
        return nm._send_direct_frame(universe, data)
    return nm._final_output_tap.invoke_source(nm._send_direct_frame, record)


def _deliver_batch(nm, batch):
    if nm._dispatch_via_router(batch):
        return
    if nm._final_output_tap is None:
        for universe, data in batch.items():
            nm._send_direct_frame(universe, data)
        return
    for universe, data in batch.items():
        _send_prepared_frame(nm, universe, data)


def _prepared_records(tap, packets, batch):
    """Project only explicit identities through the original final transform."""
    identities = tap.batch_identities(packets) or {}
    return [OutputRecord(universe_1based=universe, data=data,
                         identity=identities.get(universe))
            for universe, data in batch.items()]


def deliver_prepared_batch(nm, packets, batch):
    tap = nm._final_output_tap
    if tap is None:
        return _deliver_batch(nm, batch)
    control = tap.control
    if control is None or not control.enabled:
        return _deliver_batch(nm, batch)
    try:
        records = _prepared_records(tap, packets, batch)
        prepared = list(batch.items())
    except Exception:
        records, prepared = None, ()
    # Pairing setup precedes the sole body; do not catch/retry its exception.
    return tap.invoke_batch(lambda _: _deliver_batch(nm, batch), prepared, records)


def call_actual_sender(nm, sender, wire_universe, data, *, protocol,
                       universe_1based):
    tap = nm._final_output_tap
    if tap is None:
        return sender(wire_universe, data)
    return tap.call_sender(sender, wire_universe, data, protocol=protocol,
                           universe_1based=universe_1based)
