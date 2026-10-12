"""Read-only observations of attempted final DMX payloads; no output or evaluation.

The owner must carry an epoch token from the producing/routing lifecycle. Reading
the current epoch only at a stale queued sink cannot establish that provenance.
Disabled observations do not inspect payloads, copy bytes, call a clock or lock.

References: CPython v3.13.13 Lib/dataclasses.py:668/1277, Lib/threading.py:120,
Objects/memoryobject.c:2263 (PSF); existing OutputRouter latest-value semantics.
https://github.com/python/cpython/blob/v3.13.13/Objects/memoryobject.c#L2263
Adoption: ai-memory/_references/r185-final-output-observer-adoption.md.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from math import isfinite
from threading import Lock
from time import monotonic

DMX_SLOTS = 512
_UNIVERSE_LIMITS = {"Art-Net": 32768, "sACN": 63999}


def _valid_universe(protocol: str, universe: object) -> bool:
    if type(protocol) is not str or type(universe) is not int:
        return False
    limit = _UNIVERSE_LIMITS.get(protocol)
    return limit is not None and 1 <= universe <= limit


def _checked_domain(protocol: str, universes: Iterable[int]) -> frozenset[int]:
    if type(protocol) is not str or protocol not in _UNIVERSE_LIMITS:
        raise ValueError("protocol must be 'Art-Net' or 'sACN'")
    domain: set[int] = set()
    for universe in universes:
        if not _valid_universe(protocol, universe):
            raise ValueError("invalid UI 1-based universe for this protocol")
        domain.add(universe)
    return frozenset(domain)


def _checked_enabled(enabled: bool) -> bool:
    if type(enabled) is not bool:
        raise TypeError("enabled must be bool")
    return enabled


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _valid_attempt_time(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return isfinite(value) and value >= 0
    except OverflowError:
        return False


def _validate_frame_payload(payload: bytes) -> None:
    if type(payload) is not bytes:
        raise TypeError("frame payload must be immutable bytes")
    if len(payload) != DMX_SLOTS:
        raise ValueError("frame payload must contain exactly 512 bytes")


def _copy_payload(payload: object) -> bytes | None:
    """Copy one contiguous unsigned-byte buffer; never normalize sender data."""
    try:
        with memoryview(payload) as view:
            if not _valid_payload_view(view):
                return None
            return view.tobytes()
    except (TypeError, ValueError, BufferError):
        return None


def _valid_payload_view(view: memoryview) -> bool:
    return (view.ndim == 1 and view.itemsize == 1 and view.format == "B"
            and view.c_contiguous and view.nbytes == DMX_SLOTS)


@dataclass(frozen=True, slots=True, kw_only=True)
class FinalOutputFrame:
    """Attempted final bytes, not socket success or receiver acknowledgement."""

    protocol: str
    universe_1based: int
    payload: bytes = field(repr=False)
    epoch: int
    attempt_revision: int
    attempted_at_monotonic: float

    def __post_init__(self) -> None:
        _validate_frame_payload(self.payload)
        _validate_frame_metadata(self)


def _validate_frame_metadata(frame: FinalOutputFrame) -> None:
    if not _valid_universe(frame.protocol, frame.universe_1based):
        raise ValueError("invalid protocol or UI 1-based universe")
    if not _positive_int(frame.epoch):
        raise ValueError("epoch must be a positive integer")
    if not _positive_int(frame.attempt_revision):
        raise ValueError("attempt revision must be a positive integer")
    if not _valid_attempt_time(frame.attempted_at_monotonic):
        raise ValueError("attempt time must be finite and nonnegative")


@dataclass(frozen=True, slots=True, kw_only=True)
class _Binding:
    epoch: int
    protocol: str
    universes: frozenset[int]
    enabled: bool


def _matches_binding(binding: _Binding, *, epoch: int, protocol: str,
                     universe_1based: int) -> bool:
    return (type(epoch) is int and epoch == binding.epoch
            and type(protocol) is str and protocol == binding.protocol
            and type(universe_1based) is int
            and universe_1based in binding.universes)


@dataclass(frozen=True, slots=True, kw_only=True)
class FinalOutputReader:
    """Public consumer facade; only latest(), epoch and enabled are exposed."""

    _read_latest: Callable[[Iterable[int] | None], tuple[FinalOutputFrame, ...]] = field(repr=False)
    _read_epoch: Callable[[], int] = field(repr=False)
    _read_enabled: Callable[[], bool] = field(repr=False)

    def latest(self, *, universes: Iterable[int] | None = None) -> tuple[FinalOutputFrame, ...]:
        return self._read_latest(universes)

    @property
    def epoch(self) -> int:
        return self._read_epoch()

    @property
    def enabled(self) -> bool:
        return self._read_enabled()


class FinalOutputObserver:
    """Owner-side writer with one immutable latest frame per allowed universe.

    The finite domain is supplied by the existing output/subscription owner;
    it is not a fixed preview capacity that silently truncates a large project.
    reset_epoch() must run at configuration/project/session binding boundaries.
    """

    def __init__(self, *, protocol: str = "Art-Net", universes: Iterable[int] = (),
                 enabled: bool = False, clock: Callable[[], float] = monotonic) -> None:
        domain = _checked_domain(protocol, universes)
        self._binding = _Binding(epoch=1, protocol=protocol, universes=domain,
                                 enabled=_checked_enabled(enabled))
        self._lock = Lock()
        self._frames: dict[int, FinalOutputFrame] = {}
        self._revision = 0
        self._clock = clock

    def reader(self) -> FinalOutputReader:
        return FinalOutputReader(_read_latest=self._latest,
                                 _read_epoch=self._read_epoch,
                                 _read_enabled=self._read_enabled)

    @property
    def epoch(self) -> int:
        return self._read_epoch()

    @property
    def enabled(self) -> bool:
        return self._read_enabled()

    def set_enabled(self, *, enabled: bool) -> int:
        target = _checked_enabled(enabled)
        with self._lock:
            binding = self._binding
            if binding.enabled != target:
                self._replace_binding(protocol=binding.protocol,
                                      universes=binding.universes, enabled=target)
            return self._binding.epoch

    def reset_epoch(self, *, protocol: str, universes: Iterable[int]) -> int:
        domain = _checked_domain(protocol, universes)
        with self._lock:
            self._replace_binding(protocol=protocol, universes=domain,
                                  enabled=self._binding.enabled)
            return self._binding.epoch

    def _replace_binding(self, *, protocol: str, universes: frozenset[int],
                         enabled: bool) -> None:
        """Replace under the owner lock; old in-flight bindings cannot commit."""
        self._binding = _Binding(epoch=self._binding.epoch + 1, protocol=protocol,
                                 universes=universes, enabled=enabled)
        self._frames.clear()
        self._revision = 0

    def observe_attempt(self, *, epoch: int, protocol: str,
                        universe_1based: int, payload: object) -> bool:
        binding = self._binding
        if not binding.enabled:
            return False
        if not _matches_binding(binding, epoch=epoch, protocol=protocol,
                                universe_1based=universe_1based):
            return False
        revision = self._reserve_revision(binding)
        if revision is None:
            return False
        frame = self._capture_frame(binding=binding, revision=revision,
                                    universe_1based=universe_1based, payload=payload)
        if frame is None:
            return False
        return self._commit(binding, frame)

    def _reserve_revision(self, binding: _Binding) -> int | None:
        with self._lock:
            if binding is not self._binding:
                return None
            self._revision += 1
            return self._revision

    def _capture_frame(self, *, binding: _Binding, revision: int,
                       universe_1based: int, payload: object) -> FinalOutputFrame | None:
        try:
            attempted_at = self._clock()
            if not _valid_attempt_time(attempted_at):
                return None
            data = _copy_payload(payload)
            if data is None:
                return None
            return FinalOutputFrame(protocol=binding.protocol,
                                    universe_1based=universe_1based, payload=data,
                                    epoch=binding.epoch, attempt_revision=revision,
                                    attempted_at_monotonic=attempted_at)
        except Exception:
            # Observation is optional and must not stop the owner's real send.
            return None

    def _commit(self, binding: _Binding, frame: FinalOutputFrame) -> bool:
        with self._lock:
            if binding is not self._binding:
                return False
            previous = self._frames.get(frame.universe_1based)
            if previous is not None and previous.attempt_revision >= frame.attempt_revision:
                return False
            self._frames[frame.universe_1based] = frame
            return True

    def _read_epoch(self) -> int:
        with self._lock:
            return self._binding.epoch

    def _read_enabled(self) -> bool:
        with self._lock:
            return self._binding.enabled

    def _latest(self, universes: Iterable[int] | None) -> tuple[FinalOutputFrame, ...]:
        requested = _checked_query(universes)
        with self._lock:
            frames = tuple(self._frames.values())
        if requested is not None:
            frames = tuple(frame for frame in frames if frame.universe_1based in requested)
        return tuple(sorted(frames, key=lambda frame: frame.universe_1based))


def _checked_query(universes: Iterable[int] | None) -> frozenset[int] | None:
    if universes is None:
        return None
    requested: set[int] = set()
    for universe in universes:
        if not _positive_int(universe):
            raise ValueError("query universes must be positive integers")
        requested.add(universe)
    return frozenset(requested)
