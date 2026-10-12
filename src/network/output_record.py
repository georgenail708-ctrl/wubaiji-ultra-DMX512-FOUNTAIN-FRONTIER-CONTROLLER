"""Explicit source identity for internal borrowed output records; no sending.

The record shell is frozen; its data remains the original borrowed object.
This is not a final immutable frame, C-ring tag, queue or public preview API.
Legacy data with no identity stays unqualified; current control never repairs it.

References (semantics/API only; no upstream implementation copied):
Packet serial stays with data.
Boost lockfree@4a69f38b spsc_queue.hpp:171-183, consume before slot reuse.
https://github.com/boostorg/lockfree/blob/4a69f38b3d1d20ac56e60095a550ffb2ce4ccbd7/include/boost/lockfree/spsc_queue.hpp
CPython v3.13.13 Lib/dataclasses.py:668-688, frozen/slots/keyword-only APIs.
Adoption: ai-memory/_references/r185-output-record-adoption.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_UINT64_MAX = 2**64 - 1
_UINT16_MAX = 2**16 - 1
_ORIGIN_FIELDS = ("owner_id", "generation", "observer_epoch")


def _check_positive_uint64(name: str, value: object) -> None:
    if type(value) is not int:
        raise TypeError(f"{name} must be a built-in integer")
    if not 1 <= value <= _UINT64_MAX:
        raise ValueError(f"{name} must be between 1 and 2**64-1")


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputIdentity:
    """Production identity; submission_id is not an attempt or protocol sequence."""

    owner_id: int
    generation: int
    observer_epoch: int
    submission_id: int

    def __post_init__(self) -> None:
        for name in (*_ORIGIN_FIELDS, "submission_id"):
            _check_positive_uint64(name, getattr(self, name))


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputRecord:
    """Internal data reference, valid only for its original owner's lease.

    Consumers must finish with shared pop data before the next pop reuses it.
    Protocol/domain and payload validation belong to the original send/observer.
    """

    universe_1based: int
    data: object = field(repr=False, compare=False)
    identity: OutputIdentity | None = None

    def __post_init__(self) -> None:
        _check_positive_uint64("universe_1based", self.universe_1based)
        if self.universe_1based > _UINT16_MAX:
            raise ValueError("universe_1based must fit the existing UInt16 record")
        if self.identity is not None and type(self.identity) is not OutputIdentity:
            raise TypeError("identity must be OutputIdentity or None")


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputRecordControl:
    """Validated scalar control mirror, published by the existing output owner."""

    owner_id: int
    generation: int
    observer_epoch: int
    enabled: bool

    def __post_init__(self) -> None:
        for name in _ORIGIN_FIELDS:
            _check_positive_uint64(name, getattr(self, name))
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be bool")


def record_is_observable(record: object, *, control: OutputRecordControl) -> bool:
    """Qualify source metadata only, without touching data or minting identity."""
    if not control.enabled:
        return False
    if type(record) is not OutputRecord:
        return False
    identity = record.identity
    if identity is None:
        return False
    return (identity.owner_id == control.owner_id
            and identity.generation == control.generation
            and identity.observer_epoch == control.observer_epoch)
