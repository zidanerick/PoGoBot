import threading

import pytest

from pogobot.gestures import (
    Calibration, GestureExecutor, Rect, Trajectory, Sample, plan_straight,
)


def test_minimum_jerk_has_ordered_times_and_zero_endpoint_velocity():
    trajectory = plan_straight((.2, .8), (.7, .25), duration_ms=240,
                               interval_ms=10)
    assert trajectory.samples[0] == Sample(0, .2, .8)
    assert trajectory.samples[-1] == Sample(240, .7, .25)
    assert all(a.t_ms < b.t_ms for a, b in zip(trajectory.samples,
                                               trajectory.samples[1:]))
    # Minimum-jerk easing has a flat slope at each endpoint.
    assert trajectory.samples[1].x - .2 < .5 * (trajectory.samples[2].x - .2)
    assert .7 - trajectory.samples[-2].x < .5 * (.7 - trajectory.samples[-3].x)


def test_seeded_endpoint_jitter_is_reproducible_and_confined_to_target():
    args = dict(start=(.2, .8), end=(.7, .25), duration_ms=300,
                target_area=Rect(.65, .2, .75, .3), variation=.03, seed=71)
    a, b = plan_straight(**args), plan_straight(**args)
    assert a == b
    assert a.samples[-1] != Sample(300, .7, .25)
    assert args["target_area"].contains(a.samples[-1].x, a.samples[-1].y)
    assert all(Rect().contains(s.x, s.y) for s in a.samples)


def test_endpoint_variation_requires_a_known_target_area():
    with pytest.raises(ValueError, match="target_area"):
        plan_straight((.2, .8), (.7, .25), variation=.01)


@pytest.mark.parametrize("kwargs", [
    {"duration_ms": 15}, {"duration_ms": 5001}, {"interval_ms": 0},
    {"variation": .01}, {"max_velocity": .01},
])
def test_rejects_unsafe_or_unbounded_trajectory_parameters(kwargs):
    with pytest.raises(ValueError):
        plan_straight((.2, .8), (.7, .25), **kwargs)


def test_calibration_maps_measured_anchors_and_fits_scale_offset():
    calibration = Calibration.from_anchors(
        (1080, 2340), (.1, .1), (.9, .9), (.12, .08), (.92, .88))
    assert calibration.normalized(.1, .1) == pytest.approx((.12, .08))
    assert calibration.normalized(.9, .9) == pytest.approx((.92, .88))
    assert calibration.pixels(.1, .1) == (129, 187)
    with pytest.raises(ValueError):
        calibration.normalized(0, 0)
    with pytest.raises(ValueError):
        Calibration.from_anchors((1080, 2340), (.5, .2), (.5, .8),
                                 (.2, .2), (.8, .8))


class FakeSink:
    def __init__(self, clock=lambda: 0):
        self.events = []
        self.clock = clock
    def down(self, x, y):
        self.events.append(("down", x, y))
    def move(self, x, y):
        self.events.append(("move", x, y))
    def release(self, x, y, *, cancelled):
        self.events.append(("cancel" if cancelled else "up", x, y))


def test_executor_sends_one_ordered_pointer_and_releases_on_completion():
    now = [0.0]
    def clock(): return now[0]
    def wait(cancel, seconds):
        now[0] += seconds
        return cancel.is_set()
    sink = FakeSink()
    trajectory = plan_straight((.1, .8), (.9, .2), duration_ms=120,
                               interval_ms=20)
    result = GestureExecutor(sink, clock=clock, wait=wait).execute(
        trajectory, Calibration((100, 200)), threading.Event())
    assert result.status == "executed"
    assert result.contact_ms == pytest.approx(120)
    assert [event[0] for event in sink.events] == ["down"] + ["move"] * 6 + ["up"]
    assert result.events[-1]["action"] == "up"
    assert all(0 <= x < 100 and 0 <= y < 200
               for _, x, y in sink.events)


def test_executor_cancels_during_wait_and_releases_pointer():
    now = [0.0]
    def clock(): return now[0]
    cancel = threading.Event()
    def wait(event, seconds):
        cancel.set()
        return True
    sink = FakeSink()
    trajectory = plan_straight((.1, .8), (.9, .2), duration_ms=100,
                               interval_ms=20)
    result = GestureExecutor(sink, clock=clock, wait=wait).execute(
        trajectory, Calibration((100, 200)), cancel)
    assert result.status == "cancelled"
    assert [event[0] for event in sink.events] == ["down", "cancel"]


def test_executor_rejects_stale_display_before_touching_sink():
    sink = FakeSink()
    trajectory = plan_straight((.1, .8), (.9, .2), duration_ms=120,
                               interval_ms=20)
    with pytest.raises(ValueError, match="recalibration"):
        GestureExecutor(sink).execute(trajectory, Calibration((100, 200)),
                                      threading.Event(), screen_wh=(200, 100))
    assert sink.events == []


def test_executor_does_not_press_when_already_cancelled():
    sink = FakeSink()
    cancel = threading.Event()
    cancel.set()
    trajectory = plan_straight((.1, .8), (.9, .2), duration_ms=120)
    result = GestureExecutor(sink).execute(trajectory, Calibration((100, 200)), cancel)
    assert result.status == "cancelled"
    assert sink.events == []


def test_executor_cancels_and_releases_if_transport_misses_a_deadline():
    now = [0.0]
    def clock(): return now[0]
    def wait(cancel, seconds):
        now[0] += seconds + .1
        return cancel.is_set()
    sink = FakeSink()
    trajectory = plan_straight((.1, .8), (.9, .2), duration_ms=120,
                               interval_ms=20)
    result = GestureExecutor(sink, clock=clock, wait=wait,
                             max_lateness_ms=5).execute(
        trajectory, Calibration((100, 200)), threading.Event())
    assert result.status == "late"
    assert [event[0] for event in sink.events] == ["down", "cancel"]


def test_release_is_attempted_if_move_transport_fails():
    class BrokenSink(FakeSink):
        def move(self, x, y):
            raise OSError("transport failed")
    sink = BrokenSink()
    trajectory = plan_straight((.1, .8), (.9, .2), duration_ms=120,
                               interval_ms=20)
    with pytest.raises(OSError, match="transport failed"):
        GestureExecutor(sink).execute(trajectory, Calibration((100, 200)),
                                      threading.Event())
    assert [event[0] for event in sink.events] == ["down", "cancel"]


def test_trace_is_explicitly_planned_and_records_calibrated_device_points():
    trajectory = plan_straight((.1, .8), (.9, .2), duration_ms=120)
    trace = trajectory.trace(Calibration((100, 200)))
    assert trace["kind"] == "planned"
    assert trace["samples"][0]["device_xy"] == (10, 159)
    assert trace["duration_ms"] == 120

