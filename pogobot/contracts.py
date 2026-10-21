"""Shared runtime contracts for clocks, device snapshots, and capture identity.

This module is the first T01 integration-candidate slice. The definitions are
implemented here; Runner composition and end-to-end DeviceServices routing remain
unfinished until their own acceptance tests pass.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, NewType, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .observation import Tristate

MonotonicSeconds = NewType("MonotonicSeconds", float)
ActiveSeconds = NewType("ActiveSeconds", float)


@dataclass(frozen=True)
class FrameRef:
    """Capture-bound identity; downstream work copies this value unchanged."""

    run_id: str
    capture_epoch: int
    seq: int
    captured_mono_s: MonotonicSeconds


class Clock(Protocol):
    """Physical monotonic and pause-adjusted gameplay time."""

    def monotonic_now(self) -> MonotonicSeconds: ...
    def active_now(self) -> ActiveSeconds: ...
    def set_paused(self, paused: bool, *, at: MonotonicSeconds) -> None: ...
    def monotonic_at(self, physical_s: float) -> MonotonicSeconds: ...


class SystemClock:
    """Clock with a run-relative origin and deterministic pause accounting."""

    def __init__(self, source: Callable[[], float], *, origin: float | None = None):
        self._source = source
        self._origin = float(source()) if origin is None else float(origin)
        self._paused_total = 0.0
        self._paused_at: float | None = None
        self._last_transition = 0.0

    def monotonic_now(self) -> MonotonicSeconds:
        return MonotonicSeconds(float(self._source()) - self._origin)

    def monotonic_at(self, physical_s: float) -> MonotonicSeconds:
        """Translate one perf-counter sample into this run's monotonic domain."""
        return MonotonicSeconds(float(physical_s) - self._origin)

    @property
    def physical_origin(self) -> float:
        """Internal capture adapter seam; never serialize this process timestamp."""
        return self._origin

    def active_now(self) -> ActiveSeconds:
        mono = float(self.monotonic_now())
        paused = self._paused_total
        if self._paused_at is not None:
            paused += mono - self._paused_at
        return ActiveSeconds(mono - paused)

    def set_paused(self, paused: bool, *, at: MonotonicSeconds) -> None:
        stamp = float(at)
        if stamp < self._last_transition:
            raise ValueError("pause transitions must use nondecreasing monotonic time")
        self._last_transition = stamp
        if paused and self._paused_at is None:
            self._paused_at = stamp
        elif not paused and self._paused_at is not None:
            self._paused_total += stamp - self._paused_at
            self._paused_at = None


@dataclass(frozen=True)
class DeviceSnapshot:
    """Cached device facts sampled at one physical monotonic time."""

    sampled_mono_s: MonotonicSeconds
    foreground: Tristate
    keyboard: Tristate
    online: Tristate
    logical_wh: tuple[int, int] | None = None


class DeviceServices(Protocol):
    """Nonblocking read-only device facts; replay implementations must be fake."""

    def snapshot(self, now_mono_s: MonotonicSeconds) -> DeviceSnapshot: ...
    def close(self) -> None: ...
