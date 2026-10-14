"""Independent complete-packet/policy controls. STATIC: none have been run."""
import ctypes as ct
import math
import os
from pathlib import Path
import struct

import pytest

from sntc_abi import Binding, Clock, Packet, Snapshot, raw

PLAY0 = bytes.fromhex('534e5443190000000001444550450270')
STOP0 = bytes.fromhex('534e544319000000000044455045026f')
JUMP0 = bytes.fromhex('534e5443190000000002444550450271')
FRAME60 = bytes.fromhex('534e54433c0102031e014445504502b7')  # 01:02:03:30 @60 ->3723.5
LEGACY0 = bytes.fromhex('534e5443000000000001444550450257')
LIBERA_XOR = bytes.fromhex('534e5443190000000000444550450205')
PEER = raw(bytes((192, 168, 1, 10)))
OTHER_PEER = raw(bytes((192, 168, 1, 11)))


@pytest.fixture
def native():
    path = os.environ.get('QASCADE_SNTC_RESEARCH_DLL')
    assert path and Path(path).is_file(), 'Actual new native DLL required, no skip/mock/Python substitute'
    return Binding(path)


def packet(fields):
    # Independent wire fact oracle: not a call to candidate encoder/decoder.
    prefix = bytes(fields)
    assert len(prefix) == 14
    return prefix + sum(prefix).to_bytes(2, 'big')


def start(native, *, position=0., playing=0, known=0, enabled=1):
    state, view = Clock(), Snapshot()
    assert native.init(ct.byref(state), ct.sizeof(state), position, playing, known, ct.byref(view)) == 0
    assert native.enabled(ct.byref(state), enabled, ct.byref(view)) == 0
    return state, view


def ingest(native, state, payload, now, *, peer=PEER, caller_playing=0):
    retained, view = raw(payload), Snapshot()
    status = native.ingest(ct.byref(state), retained, len(retained), peer, now, caller_playing, ct.byref(view))
    return status, view


def sample(native, state, now):
    view = Snapshot()
    assert native.sample(ct.byref(state), now, ct.byref(view)) == 0
    return view


@pytest.mark.parametrize('payload,fps,status,position', [(PLAY0,25,1,0.),(STOP0,25,0,0.),
    (JUMP0,25,2,0.),(LEGACY0,25,1,0.),(FRAME60,60,1,3723.5)])
def test_literal_complete_original_format_packets(native, payload, fps, status, position):
    value = Packet(); retained = raw(payload)
    assert native.decode(retained, len(retained), ct.byref(value)) == 0
    assert value.abi == 1 and value.size == ct.sizeof(value)
    assert value.fps == fps and value.wire_status == status and bytes(value.sender) == b'DEPE'
    assert struct.pack('d', value.position) == struct.pack('d', position)


@pytest.mark.parametrize('fps,frame', [(1,0),(24,1),(25,24),(30,29),(60,59),(120,119),(255,254)])
def test_integer_rate_transport_and_max_valid_time_without_dropframe_invention(native, fps, frame):
    payload = packet(b'SNTC' + bytes((fps,23,59,59,frame,1)) + b'DEPE')
    value = Packet(); retained = raw(payload)
    assert native.decode(retained, 16, ct.byref(value)) == 0
    expected = 23.0*3600.0 + 59.0*60.0 + 59.0 + frame/fps
    assert struct.pack('d', value.position) == struct.pack('d', expected)


BAD = [(PLAY0[:n],2) for n in (0,1,15)] + [(PLAY0+b'\0',2),
    (b'ARTC'+PLAY0[4:],3), (PLAY0[:-1]+b'\0',4), (LIBERA_XOR,4)]
for index, value in ((5,24),(6,60),(7,60),(8,25),(9,3),(9,255)):
    prefix = bytearray(PLAY0[:14]); prefix[index] = value
    BAD.append((packet(prefix),6 if index == 9 else 5))


@pytest.mark.parametrize('payload,expected', BAD)
def test_invalid_complete_packet_before_any_clock_or_output_write(native, payload, expected):
    state, _ = start(native)
    assert ingest(native, state, PLAY0, 100)[0] == 0
    view, parsed = Snapshot(), Packet()
    ct.memset(ct.addressof(view),0xA5,ct.sizeof(view));ct.memset(ct.addressof(parsed),0xA5,ct.sizeof(parsed))
    before = bytes(state), bytes(view), bytes(parsed); retained = raw(payload)
    assert native.decode(retained, len(retained), ct.byref(parsed)) == expected
    assert native.ingest(ct.byref(state),retained,len(retained),PEER,200,0,ct.byref(view)) == expected
    assert (bytes(state),bytes(view),bytes(parsed)) == before


def test_enable_without_signal_never_seizes_local_pointer(native):
    state, view = start(native, position=7., playing=1, known=1)
    assert not view.selected and not view.fresh and view.local_allowed and view.playing == 1
    assert native.release_for_local(ct.byref(state),10,9.,1,ct.byref(view)) == 0
    assert view.position == 9. and view.playing == 1 and not view.selected


def test_duplicate_refreshes_freshness_without_numeric_revision_seek_or_stop(native):
    state, _ = start(native)
    status, first = ingest(native,state,FRAME60,100)
    assert status == 0 and first.fresh and first.selected and first.playing == 1 and not first.local_allowed
    status, repeated = ingest(native,state,FRAME60,1_400_000_100)
    assert status == 0 and repeated.revision == first.revision and repeated.position == first.position
    assert repeated.last_rx_ns == 1_400_000_100 and repeated.playing == 1
    fresh = sample(native,state,2_899_999_999)
    stale = sample(native,state,2_900_000_100)
    assert fresh.fresh and not fresh.local_allowed
    assert stale.selected and not stale.fresh and stale.local_allowed
    assert stale.position == first.position and stale.playing == 1 and stale.revision == first.revision


@pytest.mark.parametrize('other_peer,other_sender', [(True,False),(False,True),(True,True)])
def test_first_peer_and_sender_lock_rejects_others_before_write_then_allows_stale_takeover(native, other_peer, other_sender):
    state, _ = start(native)
    status, first = ingest(native,state,FRAME60,0)
    assert status == 0
    payload = STOP0 if not other_sender else packet(STOP0[:10] + b'OTHR')
    peer = OTHER_PEER if other_peer else PEER
    out = Snapshot();ct.memset(ct.addressof(out),0xA5,ct.sizeof(out));before=bytes(state),bytes(out)
    data = raw(payload)
    assert native.ingest(ct.byref(state),data,16,peer,1_499_999_999,0,ct.byref(out)) == 8
    assert (bytes(state),bytes(out)) == before
    status, current = ingest(native,state,payload,1_500_000_000,peer=peer)
    assert status == 0 and current.selected and current.fresh and current.playing == 0
    assert bytes(current.peer_ipv4) == bytes(peer) and bytes(current.sender) == payload[10:14]


def test_backward_jump_and_gap_never_infer_stop_but_explicit_stop_does(native):
    state, _ = start(native)
    assert ingest(native,state,FRAME60,1)[0] == 0
    status, moved = ingest(native,state,JUMP0,2)
    assert status == 0 and moved.position == 0. and moved.playing == 1 and moved.wire_status == 2
    stale = sample(native,state,2_000_000_002)
    assert stale.position == 0. and stale.playing == 1 and stale.selected and not stale.fresh
    status, stopped = ingest(native,state,STOP0,2_000_000_003)
    assert status == 0 and stopped.playing == 0 and stopped.position == 0. and stopped.fresh


@pytest.mark.parametrize('caller', [0,1])
def test_first_jump_unknown_playing_preserves_raw_current_caller_state(native, caller):
    state, _ = start(native, known=0)
    status, view = ingest(native,state,JUMP0,0,caller_playing=caller)
    assert status == 0 and view.have_playing and view.playing == caller and view.wire_status == 2


def test_local_drag_locked_while_fresh_and_force_off_lifecycle_releases_without_stop(native):
    state, _ = start(native)
    status, first = ingest(native,state,FRAME60,1)
    out=Snapshot();ct.memset(ct.addressof(out),0xA5,ct.sizeof(out));before=bytes(state),bytes(out)
    assert native.release_for_local(ct.byref(state),1_000,20.,0,ct.byref(out)) == 10
    assert (bytes(state),bytes(out)) == before
    assert native.force_release(ct.byref(state),ct.byref(out)) == 0
    assert not out.enabled and not out.selected and out.local_allowed
    assert out.playing == 1 and out.position == first.position and out.revision == first.revision


def test_stale_local_takeover_is_explicit_and_preserves_chosen_actual_transport(native):
    state, _ = start(native)
    assert ingest(native,state,FRAME60,10)[0] == 0
    view=sample(native,state,1_500_000_010)
    assert view.selected and view.playing == 1 and view.local_allowed
    assert native.release_for_local(ct.byref(state),1_500_000_011,8.,1,ct.byref(view)) == 0
    assert view.position == 8. and view.playing == 1 and not view.selected and view.enabled


def test_bad_abi_backward_ns_and_revision_overflow_before_write(native):
    state, _ = start(native)
    assert ingest(native,state,PLAY0,100)[0] == 0
    out=Snapshot();ct.memset(ct.addressof(out),0xA5,ct.sizeof(out));before=bytes(state),bytes(out)
    assert native.sample(ct.byref(state),99,ct.byref(out)) == 7
    assert (bytes(state),bytes(out)) == before
    state.revision=2**64-1;before=bytes(state),bytes(out)
    data=raw(FRAME60)
    assert native.ingest(ct.byref(state),data,16,PEER,101,0,ct.byref(out)) == 5
    assert (bytes(state),bytes(out)) == before
    state.size=0;before=bytes(state),bytes(out)
    assert native.sample(ct.byref(state),101,ct.byref(out)) == 1
    assert (bytes(state),bytes(out)) == before
