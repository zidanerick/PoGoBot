"""Pure straight-gesture planning and opt-in continuous-pointer execution.

No device imports or I/O live here. A transport must implement PointerSink using one
continuous pointer, not a sequence of independent swipes. Stock autoplay deliberately
continues using Swipe until a transport passes device validation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import random
import threading
import time
from typing import Protocol


def _finite(*values: float) -> None:
    if not all(math.isfinite(v) for v in values):
        raise ValueError("gesture values must be finite")


@dataclass(frozen=True)
class Rect:
    left: float = 0.0
    top: float = 0.0
    right: float = 1.0
    bottom: float = 1.0

    def __post_init__(self):
        _finite(self.left, self.top, self.right, self.bottom)
        if not (0 <= self.left <= self.right <= 1 and
                0 <= self.top <= self.bottom <= 1):
            raise ValueError("safe area must be an ordered normalized rectangle")

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


@dataclass(frozen=True)
class Calibration:
    """Capture-normalized to logical display-normalized axis scale and offset.

    Fit from two distinct measured anchors per axis. Insets/cropping can be represented;
    rotation cannot. Recalibrate after rotation, resolution, crop or UI changes.
    No guessed latency compensation is added to the requested contact duration.
    """
    screen_wh: tuple[int, int]
    scale_x: float = 1.0
    scale_y: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0

    def __post_init__(self):
        if len(self.screen_wh) != 2 or any(
            type(v) is not int or v < 2 for v in self.screen_wh
        ):
            raise ValueError("logical screen dimensions must be integers >= 2")
        _finite(self.scale_x, self.scale_y, self.offset_x, self.offset_y)
        if self.scale_x <= 0 or self.scale_y <= 0:
            raise ValueError("axis scales must be positive; rotation is unsupported")

    @classmethod
    def from_anchors(cls, screen_wh, source_a, source_b, device_a, device_b):
        """Fit normalized source/device anchors; device anchors are normalized too."""
        for point in (source_a, source_b, device_a, device_b):
            _finite(*point)
            if len(point) != 2 or not Rect().contains(*point):
                raise ValueError("anchors must be normalized coordinate pairs")
        dx, dy = source_b[0] - source_a[0], source_b[1] - source_a[1]
        if abs(dx) < 1e-6 or abs(dy) < 1e-6:
            raise ValueError("anchors must span both axes")
        sx = (device_b[0] - device_a[0]) / dx
        sy = (device_b[1] - device_a[1]) / dy
        return cls(screen_wh, sx, sy, device_a[0] - sx * source_a[0],
                   device_a[1] - sy * source_a[1])

    def normalized(self, x: float, y: float) -> tuple[float, float]:
        _finite(x, y)
        result = (x * self.scale_x + self.offset_x,
                  y * self.scale_y + self.offset_y)
        if not Rect().contains(*result):
            raise ValueError("calibration maps gesture outside the logical display")
        return result

    def pixels(self, x: float, y: float) -> tuple[int, int]:
        x, y = self.normalized(x, y)
        w, h = self.screen_wh
        return round(x * (w - 1)), round(y * (h - 1))


@dataclass(frozen=True)
class Sample:
    t_ms: int
    x: float
    y: float


@dataclass(frozen=True)
class Trajectory:
    samples: tuple[Sample, ...]
    seed: int

    def __post_init__(self):
        if not 2 <= len(self.samples) <= 1001:
            raise ValueError("trajectory requires 2..1001 samples")
        previous = -1
        for s in self.samples:
            _finite(s.x, s.y)
            if type(s.t_ms) is not int or s.t_ms <= previous:
                raise ValueError("timestamps must be strictly increasing integer ms")
            if not Rect().contains(s.x, s.y):
                raise ValueError("trajectory is outside the normalized screen")
            previous = s.t_ms
        if self.samples[0].t_ms != 0 or not 16 <= previous <= 5000:
            raise ValueError("trajectory must start at zero and last 16..5000 ms")

    @property
    def duration_ms(self) -> int:
        return self.samples[-1].t_ms

    def trace(self, calibration: Calibration) -> dict:
        """Serializable intent, not evidence of injected events or a game outcome."""
        return {"schema": 1, "kind": "planned", "seed": self.seed,
                "duration_ms": self.duration_ms, "calibration": asdict(calibration),
                "samples": [dict(asdict(s), device_xy=calibration.pixels(s.x, s.y))
                            for s in self.samples]}

    def wire_command(self, calibration: Calibration) -> str:
        """Encode one bounded plan for the Android gesture process stdin protocol."""
        samples = []
        for sample in self.samples:
            x, y = calibration.pixels(sample.x, sample.y)
            samples.append(f"{sample.t_ms},{x},{y}")
        return "PLAN " + ";".join(samples)


def plan_straight(start: tuple[float, float], end: tuple[float, float], *,
                  duration_ms: int = 200, interval_ms: int = 8,
                  safe_area: Rect = Rect(), target_area: Rect | None = None,
                  variation: float = 0.0, seed: int = 0,
                  max_velocity: float = 20.0) -> Trajectory:
    """Minimum-jerk motion with seeded endpoint variation inside a known safe area.

    Velocity is Euclidean normalized-screen distance / second, not pixels / second.
    The analytic peak is bounded before sampling. This models a straight drag, not a
    validated ballistic throw; a throw may need a different release velocity profile.
    """
    _finite(*start, *end, variation, max_velocity)
    if type(duration_ms) is not int or not 16 <= duration_ms <= 5000:
        raise ValueError("duration_ms must be an integer in 16..5000")
    if type(interval_ms) is not int or not 1 <= interval_ms <= duration_ms:
        raise ValueError("interval_ms must be in 1..duration_ms")
    if variation < 0 or max_velocity <= 0:
        raise ValueError("variation must be nonnegative and max_velocity positive")
    if not safe_area.contains(*start) or not safe_area.contains(*end):
        raise ValueError("start and end must be inside safe_area")
    if variation and target_area is None:
        raise ValueError("variation requires an explicitly known target_area")
    target = target_area or Rect(end[0], end[1], end[0], end[1])
    if not target.contains(*end):
        raise ValueError("nominal endpoint must be inside target_area")
    x_low, x_high = max(safe_area.left, target.left, end[0] - variation), min(
        safe_area.right, target.right, end[0] + variation)
    y_low, y_high = max(safe_area.top, target.top, end[1] - variation), min(
        safe_area.bottom, target.bottom, end[1] + variation)
    rng = random.Random(seed)
    # Sample inside the intersection rather than clipping a sampled point at an edge.
    ex, ey = rng.uniform(x_low, x_high), rng.uniform(y_low, y_high)
    if 1.875 * math.hypot(ex - start[0], ey - start[1]) / (duration_ms / 1000) > max_velocity:
        raise ValueError("requested duration exceeds peak velocity limit")
    steps = math.ceil(duration_ms / interval_ms)
    if steps > 1000:
        raise ValueError("trajectory exceeds 1000 segments")
    samples = []
    for i in range(steps + 1):
        t = round(i * duration_ms / steps)
        u = t / duration_ms
        eased = u ** 3 * (10 + u * (-15 + 6 * u))
        # Explicit endpoints avoid floating point excursions outside the rectangle.
        x = start[0] if i == 0 else ex if i == steps else start[0] + (ex - start[0]) * eased
        y = start[1] if i == 0 else ey if i == steps else start[1] + (ey - start[1]) * eased
        samples.append(Sample(t, x, y))
    return Trajectory(tuple(samples), seed)


class PointerSink(Protocol):
    """Synchronous acknowledged events on ONE pointer and one ordered transport.

    Implementations must bound each call, raise on rejection, and use a stable pointer
    ID/downTime. release(cancelled=True) must inject ACTION_CANCEL. They must release
    even after a partially failed down. Return values alone do not confirm a game result.
    """
    def down(self, x: int, y: int) -> None: ...
    def move(self, x: int, y: int) -> None: ...
    def release(self, x: int, y: int, *, cancelled: bool) -> None: ...


@dataclass(frozen=True)
class Execution:
    status: str
    host_delay_ms: float
    contact_ms: float
    events: tuple[dict, ...]


class GestureExecutor:
    """Serialized execution with cancellation and mandatory release attempt.

    Cancellation wakes scheduled waits. An in-flight sink call cannot be interrupted;
    cancellation latency includes the sink's documented timeout. A failed release raises
    and must halt/recover the transport; this class never reports that as success.
    """
    def __init__(self, sink: PointerSink, *, clock=time.perf_counter,
                 wait=None, max_lateness_ms: float = 40):
        _finite(max_lateness_ms)
        if max_lateness_ms < 0:
            raise ValueError("max_lateness_ms must be nonnegative")
        self.sink = sink
        self.clock = clock
        self.wait = wait or (lambda event, seconds: event.wait(seconds))
        self.max_lateness_ms = max_lateness_ms
        self._lock = threading.Lock()

    def execute(self, trajectory: Trajectory, calibration: Calibration,
                cancel: threading.Event, *, submitted_at: float | None = None,
                screen_wh: tuple[int, int] | None = None) -> Execution:
        # Validate every point before touching the sink. Reject stale resolution.
        if screen_wh is not None and tuple(screen_wh) != calibration.screen_wh:
            raise ValueError("logical display changed; recalibration required")
        points = [calibration.pixels(s.x, s.y) for s in trajectory.samples]
        if submitted_at is not None:
            _finite(submitted_at)
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("a gesture is already executing")
        try:
            return self._execute(trajectory, points, cancel, submitted_at)
        finally:
            self._lock.release()

    def _execute(self, trajectory, points, cancel, submitted_at):
        begin = self.clock()
        delay = 0.0 if submitted_at is None else max(0.0, (begin - submitted_at) * 1000)
        if cancel.is_set():
            return Execution("cancelled", delay, 0.0, ())
        events = []
        last = points[0]
        status = "cancelled"
        contact_start = begin
        def record(action, xy):
            events.append({"action": action, "device_xy": xy,
                           "elapsed_ms": (self.clock() - contact_start) * 1000})
        try:
            # Set the origin immediately before DOWN; transport call time is contact time.
            contact_start = self.clock()
            self.sink.down(*last)
            record("down", last)
            for sample, point in zip(trajectory.samples[1:], points[1:]):
                deadline = contact_start + sample.t_ms / 1000
                while not cancel.is_set() and self.clock() < deadline:
                    self.wait(cancel, max(0, deadline - self.clock()))
                if cancel.is_set():
                    break
                if (self.clock() - deadline) * 1000 > self.max_lateness_ms:
                    status = "late"
                    break
                # Include the attempted coordinate in cleanup if transport partially fails.
                last = point
                self.sink.move(*point)
                record("move", point)
            else:
                status = "executed" if not cancel.is_set() else "cancelled"
        finally:
            cancelled = status != "executed" or cancel.is_set()
            self.sink.release(*last, cancelled=cancelled)
            record("cancel" if cancelled else "up", last)
        if cancel.is_set():
            status = "cancelled"
        return Execution(status, delay, (self.clock() - contact_start) * 1000, tuple(events))
