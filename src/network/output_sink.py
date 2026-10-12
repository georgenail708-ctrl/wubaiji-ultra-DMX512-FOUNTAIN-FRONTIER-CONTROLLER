"""
Qascade V1.0 — IOutputSink ABC
Abstract base class for all DMX output transmitters.

All hardware output paths (Art-Net, sACN, etc.) implement this interface
so NetworkManager can dispatch through a single contract instead of
branching on protocol type.

Usage:

    class MySender(IOutputSink):
        def send_dmx(self, universe: int, data: np.ndarray) -> None:
            ...
        def get_status(self) -> Dict[str, Any]:
            ...
        def close(self) -> None:
            ...
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict

import numpy as np

log = logging.getLogger(__name__)


class IOutputSink(ABC):
    """Abstract interface for DMX frame transmitters.

    Every sender (ArtNet, sACN, future protocols) shall satisfy this
    contract so that ``_send_to_hw`` in ``NetworkManager`` can dispatch
    without protocol-specific branching.
    """

    @abstractmethod
    def send_dmx(self, universe: int, data: np.ndarray) -> None:
        """Transmit a 512-byte DMX frame on *universe* (1-based).

        Args:
            universe: 1-based DMX universe number.
            data:     512-element uint8 array with channel values 0-255.
        """
        ...

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Return current operational status for diagnostics / UI.

        Returns a dict with at least these keys:

            protocol (str)   — "ArtNet" | "sACN" | ...
            target   (str)   — IP / multicast address
            universe (int)   — bound universe (0 = not bound / multi-universe)
            packets  (int)   — total packets sent since construction
            errors   (int)   — total send errors since construction
            alive    (bool)  — socket / sender is operational
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """Release all resources (sockets, threads, ...)."""
        ...
