"""Independent resident-edge/alias controls, STATIC ONLY, never executed.

Reuse original raw fixture/oracle; original test_sntc_v1.py remains byte-exact.
These assertions qualify the new unmounted ABI, not Main/output integration.
"""
import ctypes as ct

import pytest

from sntc_abi import Clock, Packet, Snapshot, raw
from test_sntc_v1 import (
    FRAME60, JUMP0, OTHER_PEER, PEER, PLAY0, STOP0,
    ingest, native, packet, sample, start,
)

MARKERS = ('revision', 'position_revision', 'playing_revision',
           'selection_revision', 'jump_revision')


def markers(view):
    return tuple(getattr(view, name) for name in MARKERS)


def guarded_output():
    view = Snapshot()
    ct.memset(ct.addressof(view), 0xA5, ct.sizeof(view))
    return view


@pytest.mark.parametrize('seed,caller', [(0,0), (0,1), (1,0), (1,1)])
def test_unselected_first_jump_adopts_current_caller_even_when_init_known(native, seed, caller):
    state, _ = start(native, playing=seed, known=1)
    status, view = ingest(native, state, JUMP0, 5, caller_playing=caller)
    assert status == 0 and view.selected and view.have_playing and view.playing == caller
    assert view.position == 0. and view.position_revision == 0
    assert view.selection_revision == 1 and view.jump_revision == 1
    assert view.playing_revision == int(seed != caller)
    assert view.revision == int(seed != caller)


@pytest.mark.parametrize('release', ['force_release', 'enabled_off'])
def test_off_then_on_jump_uses_new_local_playing_not_previous_external(native, release):
    state, _ = start(native, playing=1, known=1)
    status, stopped = ingest(native, state, STOP0, 5)
    assert status == 0 and stopped.playing == 0
    view = Snapshot()
    if release == 'force_release':
        assert native.force_release(ct.byref(state), ct.byref(view)) == 0
    else:
        assert native.enabled(ct.byref(state), 0, ct.byref(view)) == 0
    assert not view.selected and not view.enabled and view.playing == 0
    assert native.enabled(ct.byref(state), 1, ct.byref(view)) == 0
    status, jumped = ingest(native, state, JUMP0, 6, caller_playing=1)
    assert status == 0 and jumped.playing == 1 and jumped.selected
    assert jumped.selection_revision == stopped.selection_revision + 2
    assert jumped.playing_revision == stopped.playing_revision + 1
    assert jumped.jump_revision == 1


def test_explicit_restart_initial_seed_does_not_override_current_first_jump(native):
    state, _ = start(native, playing=0, known=1)
    assert ingest(native, state, PLAY0, 100)[0] == 0
    view = Snapshot()
    assert native.init(ct.byref(state), ct.sizeof(state), 0., 0, 1, ct.byref(view)) == 0
    assert native.enabled(ct.byref(state), 1, ct.byref(view)) == 0
    status, current = ingest(native, state, JUMP0, 0, caller_playing=1)
    assert status == 0 and current.playing == 1 and current.have_playing
    assert markers(current) == (1, 0, 1, 1, 1)


def test_selected_jump_retains_external_playing_even_after_gap(native):
    state, _ = start(native, known=1)
    assert ingest(native, state, PLAY0, 10)[0] == 0
    status, jumped = ingest(native, state, JUMP0, 11, caller_playing=0)
    assert status == 0 and jumped.playing == 1 and jumped.jump_revision == 1
    stale = sample(native, state, 1_500_000_011)
    assert stale.selected and not stale.fresh and stale.playing == 1
    assert markers(stale) == markers(jumped)
    status, duplicate = ingest(native, state, JUMP0, 1_500_000_012, caller_playing=0)
    assert status == 0 and duplicate.playing == 1 and duplicate.fresh
    assert markers(duplicate) == markers(jumped)


def test_first_valid_stop_at_zero_has_selection_edge_without_fake_numeric_change(native):
    state, initial = start(native, known=1)
    assert markers(initial) == (0, 0, 0, 0, 0)
    status, first = ingest(native, state, STOP0, 10)
    assert status == 0 and first.selected and first.position == 0. and first.playing == 0
    assert markers(first) == (0, 0, 0, 1, 0)
    status, repeated = ingest(native, state, STOP0, 20)
    assert status == 0 and repeated.last_rx_ns == 20 and markers(repeated) == markers(first)
    assert markers(sample(native, state, 1_500_000_020)) == markers(first)


@pytest.mark.parametrize('peer,sender', [(OTHER_PEER,b'DEPE'), (PEER,b'OTHR'), (OTHER_PEER,b'OTHR')])
def test_stale_source_transition_at_equal_position_and_playing_is_observable(native, peer, sender):
    state, _ = start(native, known=1)
    assert ingest(native, state, STOP0, 0)[0] == 0
    payload = packet(STOP0[:10] + sender)
    status, current = ingest(native, state, payload, 1_500_000_000, peer=peer)
    assert status == 0 and bytes(current.peer_ipv4) == bytes(peer) and bytes(current.sender) == sender
    assert markers(current) == (0, 0, 0, 2, 0)


def test_jump_transition_new_position_and_repeated_position_are_distinct(native):
    state, _ = start(native, known=1)
    status, first = ingest(native, state, PLAY0, 0)
    assert status == 0 and markers(first) == (1, 0, 1, 1, 0)
    status, jumped = ingest(native, state, JUMP0, 1)
    assert status == 0 and markers(jumped) == (1, 0, 1, 1, 1)
    assert markers(ingest(native, state, JUMP0, 2)[1]) == markers(jumped)
    one_frame = packet(b'SNTC' + bytes((25,0,0,0,1,2)) + b'DEPE')
    status, moved = ingest(native, state, one_frame, 3)
    assert status == 0 and moved.position == 1. / 25.
    assert markers(moved) == (2, 1, 1, 1, 2)
    assert markers(ingest(native, state, one_frame, 4)[1]) == markers(moved)
    stopped = packet(one_frame[:9] + b'\0' + one_frame[10:14])
    status, last_stop = ingest(native, state, stopped, 5)
    assert status == 0 and last_stop.playing == 0 and markers(last_stop) == (3, 1, 2, 1, 2)
    status, same_position_jump = ingest(native, state, one_frame, 6, caller_playing=1)
    assert status == 0 and same_position_jump.playing == 0
    assert markers(same_position_jump) == (3, 1, 2, 1, 3)
    assert markers(sample(native, state, 7)) == markers(same_position_jump)


def test_coalesced_snapshot_retains_jump_edge_after_many_identical_packets(native):
    state, _ = start(native, known=1)
    assert ingest(native, state, PLAY0, 0)[0] == 0
    assert ingest(native, state, JUMP0, 1)[0] == 0
    for now in (2, 3, 4):
        status, view = ingest(native, state, JUMP0, now)
        assert status == 0 and markers(view) == (1, 0, 1, 1, 1)
    final = sample(native, state, 23_000_000)
    assert markers(final) == (1, 0, 1, 1, 1) and final.playing == 1


def test_stale_explicit_local_takeover_changes_selection_only_when_same_numeric_state(native):
    state, _ = start(native, known=1)
    status, first = ingest(native, state, PLAY0, 0)
    assert status == 0
    view = Snapshot()
    assert native.release_for_local(ct.byref(state), 1_500_000_000, 0., 1, ct.byref(view)) == 0
    assert not view.selected and view.local_allowed and view.playing == 1 and view.position == 0.
    assert markers(view) == (first.revision, first.position_revision,
                             first.playing_revision, first.selection_revision + 1, first.jump_revision)


@pytest.mark.parametrize('name,payload,peer,now', [
    ('revision', FRAME60, PEER, 1), ('position_revision', FRAME60, PEER, 1),
    ('playing_revision', STOP0, PEER, 1), ('selection_revision', PLAY0, OTHER_PEER, 1_500_000_000),
    ('jump_revision', JUMP0, PEER, 1),
])
def test_any_required_marker_overflow_rejects_entire_packet_before_writes(native, name, payload, peer, now):
    state, _ = start(native, known=1)
    assert ingest(native, state, PLAY0, 0)[0] == 0
    setattr(state, name, 2**64 - 1)
    view, data = guarded_output(), raw(payload)
    before = bytes(state), bytes(view)
    assert native.ingest(ct.byref(state), data, 16, peer, now, 0, ct.byref(view)) == 5
    assert (bytes(state), bytes(view)) == before


@pytest.mark.parametrize('release', ['force_release', 'enabled_off'])
def test_lifecycle_off_always_unlocks_even_if_selection_marker_saturated(native, release):
    state, _ = start(native, known=1)
    status, first = ingest(native, state, FRAME60, 0)
    assert status == 0
    state.selection_revision = 2**64 - 1
    view = Snapshot()
    if release == 'force_release':
        result = native.force_release(ct.byref(state), ct.byref(view))
    else:
        result = native.enabled(ct.byref(state), 0, ct.byref(view))
    assert result == 0 and not view.selected and not view.enabled and view.local_allowed
    assert view.position == first.position and view.playing == first.playing and view.revision == first.revision
    assert view.selection_revision == 2**64 - 1


def test_stale_local_takeover_overflow_is_rejected_before_writes(native):
    state, _ = start(native, known=1)
    assert ingest(native, state, PLAY0, 0)[0] == 0
    state.selection_revision = 2**64 - 1
    view = guarded_output(); before = bytes(state), bytes(view)
    assert native.release_for_local(ct.byref(state), 1_500_000_000, 0., 1, ct.byref(view)) == 5
    assert (bytes(state), bytes(view)) == before


def test_decode_declared_input_output_alias_rejected_without_any_write(native):
    parsed = Packet()
    ct.memmove(ct.addressof(parsed), PLAY0, 16)
    before = bytes(parsed)
    pointer = ct.cast(ct.byref(parsed), ct.POINTER(ct.c_uint8))
    assert native.decode(pointer, 16, ct.byref(parsed)) == 1
    assert bytes(parsed) == before


@pytest.mark.parametrize('call', ['init', 'enabled', 'ingest', 'sample', 'release', 'force'])
def test_clock_and_snapshot_storage_alias_rejected_before_any_write(native, call):
    state, _ = start(native, known=1)
    before = bytes(state)
    alias = ct.cast(ct.byref(state), ct.POINTER(Snapshot))
    data = raw(PLAY0)
    args = {
        'init': lambda: native.init(ct.byref(state), ct.sizeof(state), 0., 0, 1, alias),
        'enabled': lambda: native.enabled(ct.byref(state), 0, alias),
        'ingest': lambda: native.ingest(ct.byref(state), data, 16, PEER, 0, 0, alias),
        'sample': lambda: native.sample(ct.byref(state), 0, alias),
        'release': lambda: native.release_for_local(ct.byref(state), 0, 0., 0, alias),
        'force': lambda: native.force_release(ct.byref(state), alias),
    }
    assert args[call]() == 1 and bytes(state) == before


@pytest.mark.parametrize('alias', ['packet_state', 'packet_output', 'peer_state', 'peer_output', 'packet_peer'])
def test_declared_ingest_input_aliases_rejected_before_any_write(native, alias):
    state, _ = start(native, known=1)
    view, data = guarded_output(), raw(PLAY0)
    packet_ptr, peer_ptr = data, PEER
    if alias == 'packet_state':
        packet_ptr = ct.cast(ct.byref(state), ct.POINTER(ct.c_uint8))
    elif alias == 'packet_output':
        packet_ptr = ct.cast(ct.byref(view), ct.POINTER(ct.c_uint8))
    elif alias == 'peer_state':
        peer_ptr = ct.cast(ct.byref(state), ct.POINTER(ct.c_uint8))
    elif alias == 'peer_output':
        peer_ptr = ct.cast(ct.byref(view), ct.POINTER(ct.c_uint8))
    else:
        peer_ptr = ct.cast(ct.byref(data, 10), ct.POINTER(ct.c_uint8))
    before = bytes(state), bytes(view), bytes(data)
    assert native.ingest(ct.byref(state), packet_ptr, 16, peer_ptr, 0, 0, ct.byref(view)) == 1
    assert (bytes(state), bytes(view), bytes(data)) == before
