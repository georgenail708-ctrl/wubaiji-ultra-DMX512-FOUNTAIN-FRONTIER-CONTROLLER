"""TC1-H1 source-only receiver/facade. No product/Qt imports or socket creation.

ROOT supplies qualified actual C functions/types/source-current predicate and an
artnet_node public socket factory. All numerical policy remains in one CClock.
This candidate is unmounted/unexecuted; current RAM hold prohibits execution.
"""
from __future__ import annotations

import ctypes as ct
from dataclasses import dataclass
import errno
import socket
import threading
import time
from typing import Callable


class NativeClockError(RuntimeError):
    def __init__(self, operation, status):
        self.operation, self.status = operation, status
        super().__init__(f'SNTC native {operation} rejected with status {status}')


class SourceQualificationError(RuntimeError):
    pass


class ShutdownIncomplete(RuntimeError):
    pass


@dataclass(frozen=True)
class ClockResult:
    status: int
    snapshot: object | None


@dataclass(frozen=True)
class StopReport:
    native_result: ClockResult | None
    worker_stopped: bool
    socket_closed: bool
    error: tuple[str, str] | None


class SntcInput:
    """One fixed CClock, one IPv4 socket, at most one owned receive thread.

    native: qualified actual ctypes functions named init/enabled/ingest/sample/
      release_for_local/force_release; exact clock/snapshot types are supplied.
    source_current: ROOT's admitted fast identity/ABI/pointer/owner predicate.
    socket_factory: actual artnet_node public factory, owning failure cleanup.
    current_playing: admitted raw current controller getter; called under Lock,
      must not call Qt methods, compute mathematics or mutate controller state.
    """
    def __init__(self, *, native, clock_type, snapshot_type,
                 source_current: Callable[[], bool], socket_factory,
                 current_playing: Callable[[], bool],
                 initial_position, initial_playing, initial_known=1, retirement_current=None):
        self._native = native
        self._clock = clock_type()
        self._snapshot_type = snapshot_type
        self._source_current = source_current
        self._retirement_current = retirement_current or source_current
        self._socket_factory = socket_factory
        self._current_playing = current_playing
        self._lock = threading.RLock()
        self._lifecycle = threading.Lock()
        self._socket = None
        self._thread = None
        self._stop_event = None
        self._socket_closed = True
        self._closed = False
        self._last_status = None
        self._error = None
        self._ui_token = None
        self._local_thread = None
        self._epoch = 0
        self.init(initial_position, initial_playing, initial_known)

    def _error_locked(self, stage, error):
        # One bounded coarse diagnostic; no per-packet queue or log history.
        self._error = (stage, (type(error).__name__ + ': ' + str(error))[:256])

    def _require_current_locked(self):
        if self._source_current() is not True:
            raise SourceQualificationError('Actual SNTC source/ABI/owner is not current')

    def source_current(self):
        with self._lock:
            return self._source_current() is True

    def _reject_local_lifecycle(self):
        if self._local_thread == threading.get_ident():
            raise RuntimeError('A local-controller callback cannot reenter SNTC lifecycle')

    def _call_locked(self, name, *args, retirement=False):
        if retirement:
            if self._retirement_current() is not True:
                raise SourceQualificationError('Retained native SNTC owner is not qualified for retirement')
        else:
            self._require_current_locked()
        view = self._snapshot_type()
        status = getattr(self._native, name)(ct.byref(self._clock), *args, ct.byref(view))
        self._last_status = status
        return ClockResult(status, view if status == 0 else None)

    @staticmethod
    def _required(result, operation):
        if result.status != 0:
            raise NativeClockError(operation, result.status)
        return result.snapshot

    def init(self, position, playing, known=1):
        """Explicit new consumer epoch; only while receiver is not alive."""
        self._reject_local_lifecycle()
        with self._lifecycle:
            with self._lock:
                if self._closed or (self._thread is not None and self._thread.is_alive()):
                    raise RuntimeError('Cannot reset an active/closed SNTC owner')
                result = self._call_locked('init', ct.sizeof(self._clock), position, playing, known)
                view = self._required(result, 'init')
                # Opaque owner birth token, not elapsed-time or frame mathematics.
                self._epoch = time.monotonic_ns()
                self._ui_token = None
                return view

    def _snapshot_locked(self):
        self._require_current_locked()
        # Take raw now after admission and inside the same shared Clock Lock.
        now = time.monotonic_ns()
        view = self._snapshot_type()
        status = self._native.sample(ct.byref(self._clock), now, ct.byref(view))
        self._last_status = status
        return self._required(ClockResult(status, view if status == 0 else None), 'sample')

    def native_snapshot(self):
        """Same owned Clock for the real generator/controller/UI consumers."""
        with self._lock:
            return self._snapshot_locked()

    def ordinary_generator_plan(self, binding, controller):
        # Only the qualified bridge calls this inside its real PE render Lock.
        # Clock selection and raw fields form one same-lock view; no Qt calls.
        with self._lock:
            self._require_current_locked()
            if binding._base is not self._native:
                raise SourceQualificationError('Foreign ordinary generator native owner')
            view = self._snapshot_locked()
            if view.selected == 1:
                return None
            return binding.plan(vars(controller).get('_playing'), controller)

    def media_snapshot(self):
        """One atomic raw view and birth token for the actual media consumer."""
        with self._lock:
            return self._snapshot_locked(), self._epoch

    def is_enabled(self):
        """Exact Python bool from native enabled bit, independent of worker health."""
        with self._lock:
            return self._snapshot_locked().enabled == 1

    @staticmethod
    def _token(view):
        # Read native results only. No host elapsed-time/frame/range/counter math.
        # Exclude last_rx_ns: duplicates refresh C freshness without UI seek spam.
        return (view.enabled, view.selected, view.fresh, view.local_allowed, view.have_playing,
                view.playing, view.wire_status, view.fps, view.revision,
                view.position_revision, view.playing_revision,
                view.selection_revision, view.jump_revision,
                bytes(view.peer_ipv4), bytes(view.sender))

    def poll_ui(self):
        """Coalesced latest snapshot, or None if all coarse fields are unchanged."""
        with self._lock:
            view = self._snapshot_locked()
            token = self._token(view)
            if token == self._ui_token:
                return None
            return view

    def poll_media(self):
        """One same-epoch view for coalesced UI and continuously selected media."""
        with self._lock:
            view = self._snapshot_locked()
            return view, self._epoch, self._token(view) != self._ui_token

    def acknowledge_ui(self, view):
        """Commit only after the current consumer successfully applied its view."""
        with self._lock:
            self._ui_token = self._token(view)

    @staticmethod
    def sender_peer(view):
        """Display metadata only; raw sender bytes remain lossless."""
        return socket.inet_ntoa(bytes(view.peer_ipv4)), bytes(view.sender)

    def local_takeover(self, position, playing, apply_local: Callable[[object], None]):
        """C permission/release then original local action atomically in one Lock.

        ROOT must provide the admitted original action, including stale local
        Play/Pause/seek. Status10 returns without callback/controller mutation.
        Do not split release and actual local mutation across Lock boundaries.
        """
        with self._lock:
            self._require_current_locked()
            now = time.monotonic_ns()
            view = self._snapshot_type()
            status = self._native.release_for_local(
                ct.byref(self._clock), now, position, playing, ct.byref(view))
            self._last_status = status
            result = ClockResult(status, view if status == 0 else None)
            if status == 0:
                prior = self._local_thread
                self._local_thread = threading.get_ident()
                try:
                    apply_local(view)
                finally:
                    self._local_thread = prior
            return result

    def enable(self):
        """Automatic all-interface UDP8405; no authority without a valid packet."""
        self._reject_local_lifecycle()
        with self._lifecycle:
            with self._lock:
                if self._closed:
                    raise RuntimeError('SNTC owner is closed')
                if self._thread is not None and self._thread.is_alive():
                    if self._stop_event.is_set():
                        raise ShutdownIncomplete('Previous SNTC thread has not retired')
                    return self._snapshot_locked()
                if self._socket is not None and not self._socket_closed:
                    raise ShutdownIncomplete('Previous SNTC socket has not closed; retry OFF before enabling')
            # Factory must close on any create/configure/bind failure. The current
            # discovery factory's temporary port0 cannot satisfy this contract.
            owned_socket = self._socket_factory(bind_ip='0.0.0.0', port=8405, timeout=0.2)
            stop_event, thread = None, None
            with self._lock:
                # Retain the actual resource before Event/Thread/native/start
                # can fail. A failed close must remain reachable for OFF retry.
                self._socket = owned_socket
                self._socket_closed = False
            try:
                stop_event = threading.Event()
                thread = threading.Thread(target=self._receive, args=(owned_socket, stop_event),
                                          name='Qascade-SNTC', daemon=True)
                with self._lock:
                    view = self._required(self._call_locked('enabled', 1), 'enabled')
                    self._socket, self._stop_event, self._thread = owned_socket, stop_event, thread
                    self._socket_closed = False
                    self._error = None
                    thread.start()
                    return view
            except BaseException as error:
                if stop_event is not None:
                    stop_event.set()
                try:
                    owned_socket.close()
                    with self._lock:
                        self._socket_closed = True
                except Exception as close_error:
                    with self._lock:
                        self._error_locked('start_close', close_error)
                with self._lock:
                    if (thread is None or not thread.is_alive()) and self._socket_closed:
                        self._thread, self._socket, self._stop_event = None, None, None
                    try:
                        self._call_locked('force_release', retirement=True)
                    except Exception as release_error:
                        self._error_locked('start_release', release_error)
                    self._error_locked('start', error)
                raise

    def _receive(self, owned_socket, stop_event):
        try:
            while not stop_event.is_set():
                try:
                    payload, address = owned_socket.recvfrom(17)
                except socket.timeout:
                    continue
                except OSError as error:
                    if stop_event.is_set():
                        break
                    # Winsock MSGSIZE discards oversized datagrams. Linux returns
                    # len17, which C rejects. Neither case refreshes ownership.
                    if getattr(error, 'winerror', None) == 10040 or error.errno == errno.EMSGSIZE:
                        continue
                    raise
                peer = socket.inet_aton(address[0])  # Native IPv4 IO conversion.
                retained = (ct.c_uint8 * len(payload)).from_buffer_copy(payload)
                retained_peer = (ct.c_uint8 * 4).from_buffer_copy(peer)
                with self._lock:
                    if stop_event.is_set():
                        break
                    self._require_current_locked()
                    playing = self._current_playing()  # Raw actual supplier, not init seed.
                    if type(playing) is not bool:
                        raise SourceQualificationError('Admitted current-playing getter must return raw bool')
                    now = time.monotonic_ns()
                    view = self._snapshot_type()
                    status = self._native.ingest(ct.byref(self._clock), retained, len(payload),
                        retained_peer, now, playing, ct.byref(view))
                    self._last_status = status
                    if status == 1:
                        raise NativeClockError('ingest ABI', status)
                    # No Qt signal, queue, controller/output callback or render.
        except Exception as error:
            stop_event.set()
            with self._lock:
                self._error_locked('receive', error)
            # Failure/gap never stops playback or clears native selected state.
        finally:
            try:
                owned_socket.close()
                with self._lock:
                    if self._socket is owned_socket:
                        self._socket_closed = True
            except Exception as error:
                with self._lock:
                    self._error_locked('receive_close', error)

    def _stop(self, permanent):
        self._reject_local_lifecycle()
        with self._lifecycle:
            with self._lock:
                if permanent:
                    self._closed = True
                stop_event, owned_socket, thread = self._stop_event, self._socket, self._thread
                if stop_event is not None:
                    stop_event.set()
                try:
                    released = self._call_locked('force_release', retirement=True)
                    if released.status != 0:
                        self._error_locked('stop_release', NativeClockError('force_release', released.status))
                except Exception as error:
                    released = None
                    self._error_locked('stop_release', error)
            if owned_socket is not None:
                try:
                    owned_socket.close()
                    with self._lock:
                        self._socket_closed = True
                except Exception as error:
                    with self._lock:
                        self._error_locked('stop_close', error)
            if (thread is not None and thread is not threading.current_thread()
                    and thread.is_alive()):
                try:
                    thread.join(timeout=0.75)  # Never join while holding Clock Lock.
                except Exception as error:
                    with self._lock:
                        self._error_locked('stop_join', error)
            stopped = thread is None or not thread.is_alive()
            with self._lock:
                if not stopped:
                    self._error = ('stop_join', 'Owned SNTC worker did not retire within bounded join')
                elif self._socket_closed and self._thread is thread:
                    self._thread, self._socket, self._stop_event = None, None, None
                if (stopped and self._socket_closed and released is not None
                        and released.status == 0):
                    # A successful actual retry must not display the old failed
                    # close as this attempt's current retirement result.
                    self._error = None
                return StopReport(released, stopped, self._socket_closed, self._error)

    def disable(self):
        """OFF forces C release without implicit original Stop/Pause/blackout."""
        return self._stop(False)

    def force_close(self):
        """Permanent owned lifecycle release; ROOT must inspect StopReport."""
        return self._stop(True)

    def diagnostics(self):
        """Bounded metadata, never a packet/frame history or mathematical policy."""
        with self._lock:
            return {'closed': self._closed,
                    'worker_alive': self._thread is not None and self._thread.is_alive(),
                    'socket_closed': self._socket_closed,
                    'last_native_status': self._last_status, 'error': self._error}
