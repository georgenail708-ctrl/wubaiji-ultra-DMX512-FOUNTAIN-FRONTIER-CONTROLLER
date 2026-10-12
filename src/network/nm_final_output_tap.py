"""Composition for explicit NM source and actual sender-attempt observation.

No NM/Qt/Project imports, engine, sender, queue, payload copies or extra clock.
Control/capture callers own the original NM state lock. Hot reads use an
immutable OutputRecordControl; observer alone copies final bytes once.
CPython ContextVar and existing record/observer APIs are directly reused.
Semantics only.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock

from .final_output_observer import FinalOutputObserver, FinalOutputReader
from .output_attempt_scope import current_invocation, invoke_attempt
from .output_record import (
    OutputIdentity, OutputRecord, OutputRecordControl, record_is_observable,
)

_UINT64_MAX = 2**64 - 1
_OWNER_LOCK = Lock()
_NEXT_OWNER_ID = 1


def _allocate_owner() -> int | None:
    global _NEXT_OWNER_ID
    with _OWNER_LOCK:
        if _NEXT_OWNER_ID > _UINT64_MAX:
            return None
        owner = _NEXT_OWNER_ID
        _NEXT_OWNER_ID += 1
        return owner


@dataclass(slots=True, eq=False)
class _AttemptState:
    tap: object
    invocation: object
    fact: tuple | None = None
    calls: int = 0
    notified: bool = False


@dataclass(frozen=True, slots=True, eq=False)
class _SourceState:
    tap: object
    invocation: object
    packets: object
    records: dict


_ACTIVE_ATTEMPT: ContextVar[_AttemptState | None] = ContextVar(
    "qascade_nm_actual_attempt", default=None,
)
_ACTIVE_SOURCE: ContextVar[_SourceState | None] = ContextVar(
    "qascade_nm_source_call", default=None,
)


class NMFinalOutputTap:
    """Opt-in owner adapter; controls are called under the caller's state lock.

    begin_binding invalidates before original configuration/project work;
    only commit_binding of that exact lease can qualify the result. Helpers
    cannot infer upstream engine provenance or make C/PACK/Job data current.
    """

    def __init__(self, observer: FinalOutputObserver, *, protocol="Art-Net",
                 universes=(), ready=True):
        self._observer = observer
        self._reader = observer.reader()
        self._owner_id = _allocate_owner()
        self._generation, self._submission = 1, 0
        self._protocol, self._universes = protocol, tuple(universes)
        self._control: OutputRecordControl | None = None
        self._lease, self._exhausted = None, self._owner_id is None
        self._ready = False
        self._bind(protocol=protocol, universes=self._universes, ready=ready)

    def reader(self) -> FinalOutputReader:
        return self._reader

    @property
    def control(self) -> OutputRecordControl | None:
        return self._control

    def _enabled(self) -> bool:
        control = self._control
        return control is not None and control.enabled

    def _disable(self) -> None:
        control = self._control
        if control is not None:
            self._control = OutputRecordControl(
                owner_id=control.owner_id, generation=control.generation,
                observer_epoch=control.observer_epoch, enabled=False,
            )

    def begin_binding(self):
        """Invalidate/clear first; an older finally cannot commit a new lease."""
        self._disable()
        lease = object()
        self._lease = lease
        if self._generation == _UINT64_MAX:
            self._exhausted = True
        else:
            self._generation += 1
        self._bind(protocol=self._protocol, universes=self._universes, ready=False)
        return lease

    def commit_binding(self, lease, *, protocol, universes, ready=True) -> bool:
        if lease is None or lease is not self._lease:
            return False
        self._lease = None
        return self._bind(protocol=protocol, universes=universes, ready=ready)

    def invalidate(self) -> None:
        self.begin_binding()
        self._lease = None

    def set_enabled(self, *, enabled: bool) -> bool:
        ready = self._ready
        lease = self.begin_binding()
        try:
            self._observer.set_enabled(enabled=enabled)
        except Exception:
            return False
        return self.commit_binding(lease, protocol=self._protocol,
                                   universes=self._universes, ready=ready)

    def _bind(self, *, protocol, universes, ready) -> bool:
        self._disable()
        self._ready = False
        try:
            domain = tuple(universes)
            epoch = self._observer.reset_epoch(protocol=protocol, universes=domain)
            self._protocol, self._universes = protocol, domain
            if self._exhausted or type(ready) is not bool:
                return False
            self._control = OutputRecordControl(
                owner_id=self._owner_id, generation=self._generation,
                observer_epoch=epoch, enabled=ready and self._observer.enabled,
            )
            self._ready = ready
            return True
        except Exception:
            return False

    def capture_sources(self, packets):
        """Borrow references under original NM lock; disabled never reads data."""
        if not self._enabled():
            return None
        control = self._control
        try:
            if len(packets) > _UINT64_MAX - self._submission:
                self._exhausted = True
                self._disable()
                return None
            records = []
            for universe, data in packets:
                self._submission += 1
                identity = OutputIdentity(
                    owner_id=control.owner_id, generation=control.generation,
                    observer_epoch=control.observer_epoch,
                    submission_id=self._submission,
                )
                records.append(OutputRecord(universe_1based=universe,
                                            data=data, identity=identity))
            return records
        except Exception:
            return None

    @contextmanager
    def _source_scope(self, invocation, *, packets, records, attempt):
        paired = {record.universe_1based: record for record in records or ()
                  if type(record) is OutputRecord}
        source = _SourceState(self, invocation, packets, paired)
        token = _ACTIVE_SOURCE.set(source)
        try:
            if attempt:
                with self.attempt_scope(invocation):
                    yield
            else:
                yield
        finally:
            _ACTIVE_SOURCE.reset(token)

    def invoke_batch(self, send_batch, packets, records):
        if not self._enabled():
            return send_batch(packets)
        def factory(invocation):
            return self._source_scope(invocation, packets=packets,
                                      records=records, attempt=False)
        return invoke_attempt(lambda: send_batch(packets), factory, record=None,
                              universe_1based=None, data=packets)

    def invoke_source(self, send_to_hw, record: OutputRecord):
        if not self._enabled():
            return send_to_hw(record.universe_1based, record.data)
        def factory(invocation):
            return self._source_scope(invocation, packets=None,
                                      records=(record,), attempt=True)
        def body():
            try:
                return send_to_hw(record.universe_1based, record.data)
            finally:
                self._notify(record)
        return invoke_attempt(body, factory, record=record,
                              universe_1based=record.universe_1based, data=record.data)

    def _source(self):
        if not self._enabled():
            return None
        try:
            source = _ACTIVE_SOURCE.get()
            invocation = current_invocation()
            if source is not None and source.tap is self:
                if source.invocation is invocation and invocation.entered:
                    return source
        except Exception:
            pass
        return None

    def source_identity(self, universe_1based, data):
        source = self._source()
        if source is None:
            return None
        record = source.records.get(universe_1based)
        if record_is_observable(record, control=self._control):
            if record.data is data and record.universe_1based == universe_1based:
                return record.identity
        return None

    def batch_identities(self, packets):
        source = self._source()
        if source is None or source.packets is not packets:
            return None
        identities = {}
        for universe, data in packets:
            identity = self.source_identity(universe, data)
            if identity is not None:
                identities[universe] = identity
        return identities

    @contextmanager
    def attempt_scope(self, invocation):
        state = _AttemptState(self, invocation)
        token = _ACTIVE_ATTEMPT.set(state)
        try:
            yield
        finally:
            _ACTIVE_ATTEMPT.reset(token)

    def _matching_attempt(self, state, invocation) -> bool:
        if state is None or state.tap is not self:
            return False
        return state.invocation is invocation and invocation.entered

    def _attempt(self):
        if not self._enabled():
            return None
        try:
            state = _ACTIVE_ATTEMPT.get()
            invocation = current_invocation()
            if not self._matching_attempt(state, invocation):
                return None
            record = invocation.record
            if not record_is_observable(record, control=self._control):
                return None
            if record.universe_1based == invocation.universe_1based:
                if record.data is invocation.data:
                    return state
        except Exception:
            pass
        return None

    def call_sender(self, sender_call, wire_universe, data, *, protocol,
                    universe_1based):
        """Exact original two-arg call; finally marks attempted even if it raises."""
        state = self._attempt()
        try:
            return sender_call(wire_universe, data)
        finally:
            if state is not None:
                try:
                    state.calls += 1
                    state.fact = (protocol, universe_1based, data)
                except Exception:
                    pass

    def _notify(self, record) -> None:
        try:
            self.after_record(record)
        except Exception:
            pass

    def _can_notify(self, state, record) -> bool:
        return (state is not None and state.invocation.record is record
                and state.fact is not None and state.calls == 1
                and not state.notified)

    def after_record(self, record) -> bool:
        """Scalar/source/invocation/sender checks precede the sole observer copy."""
        if not self._enabled():
            return False
        try:
            if not record_is_observable(record, control=self._control):
                return False
            state = self._attempt()
            if not self._can_notify(state, record):
                return False
            protocol, universe, data = state.fact
            if universe != record.universe_1based or protocol != self._protocol:
                return False
            state.notified = True
            return self._observer.observe_attempt(
                epoch=record.identity.observer_epoch, protocol=protocol,
                universe_1based=universe, payload=data,
            )
        except Exception:
            return False
