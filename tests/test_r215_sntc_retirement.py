"""Socket fault controls, UNRUN; not native/SNTC qualification or Main acceptance.

The actual facade owns a real UDP socket. Only native status is injected: these
tests exercise resource retention/retry, never validate clock mathematics with
fake native results or bypass product source admission as an acceptance claim.
"""
import socket
import threading
from types import SimpleNamespace

import pytest

from src.network.sntc_input import ClockResult, ShutdownIncomplete, SntcInput


class OnceCloseFails:
    def __init__(self):
        self.actual = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.calls = 0

    def close(self):
        self.calls += 1
        if self.calls == 1:
            raise OSError('injected actual socket close failure')
        self.actual.close()


class NeverStartedThread:
    def __init__(self, **_kwargs):
        self.join_calls = 0

    def start(self):
        raise RuntimeError('injected Thread.start failure')

    def is_alive(self):
        return False

    def join(self, **_kwargs):
        self.join_calls += 1
        raise RuntimeError('cannot join a never-started thread')


def facade(socket_owner, *, thread=None):
    receiver = object.__new__(SntcInput)
    receiver._lock, receiver._lifecycle = threading.RLock(), threading.Lock()
    receiver._closed, receiver._local_thread = False, None
    receiver._socket, receiver._socket_closed = socket_owner, socket_owner is None
    receiver._thread, receiver._stop_event = thread, threading.Event() if thread is not None else None
    receiver._error = None
    calls = []

    def injected_native_status(name, *args, **kwargs):
        calls.append((name, kwargs))
        return ClockResult(0, SimpleNamespace(enabled=1))

    receiver._call_locked = injected_native_status
    return receiver, calls


def test_dead_worker_close_failure_retains_same_actual_socket_for_retry():
    owner, thread = OnceCloseFails(), NeverStartedThread()
    receiver, native_calls = facade(owner, thread=thread)
    try:
        first = receiver.disable()
        assert first.worker_stopped and not first.socket_closed
        assert receiver._socket is owner and receiver._thread is thread
        second = receiver.disable()
        assert second.worker_stopped and second.socket_closed
        assert first.error is not None and second.error is None
        assert receiver._socket is None and receiver._thread is None
        assert owner.actual.fileno() == -1 and owner.calls == 2
        assert thread.join_calls == 0
        assert all(call == ('force_release', {'retirement': True}) for call in native_calls)
    finally:
        owner.actual.close()


def test_failed_start_retains_socket_refuses_second_factory_and_off_retries(monkeypatch):
    owner = OnceCloseFails()
    receiver, native_calls = facade(None)
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return owner

    receiver._socket_factory = factory
    monkeypatch.setattr(threading, 'Thread', NeverStartedThread)
    try:
        with pytest.raises(RuntimeError, match='Thread.start failure'):
            receiver.enable()
        assert receiver._socket is owner and not receiver._socket_closed
        assert owner.actual.fileno() >= 0
        with pytest.raises(ShutdownIncomplete, match='Previous SNTC socket'):
            receiver.enable()
        assert len(factory_calls) == 1
        assert ('force_release', {'retirement': True}) in native_calls
        report = receiver.disable()
        assert report.worker_stopped and report.socket_closed
        assert report.error is None
        assert receiver._socket is None and receiver._thread is None
        assert owner.actual.fileno() == -1
    finally:
        owner.actual.close()


def test_event_construction_failure_keeps_factory_socket_before_thread_exists(monkeypatch):
    owner = OnceCloseFails()
    receiver, _calls = facade(None)
    receiver._socket_factory = lambda **_kwargs: owner

    def fail_event():
        raise MemoryError('injected Event allocation failure')

    monkeypatch.setattr(threading, 'Event', fail_event)
    try:
        with pytest.raises(MemoryError, match='Event allocation'):
            receiver.enable()
        assert receiver._socket is owner and receiver._thread is None
        assert not receiver._socket_closed and owner.actual.fileno() >= 0
        report = receiver.force_close()
        assert report.worker_stopped and report.socket_closed
        assert receiver._socket is None and owner.actual.fileno() == -1
    finally:
        owner.actual.close()
