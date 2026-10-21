from pogobot.contracts import DeviceSnapshot, MonotonicSeconds, SystemClock
from pogobot.device import FakeDeviceServices, LiveDeviceServices, ReplayDeviceServices
from pogobot.observation import Tristate


def test_fake_device_services_returns_supplied_snapshot_without_io():
    expected = DeviceSnapshot(MonotonicSeconds(2.0), Tristate.TRUE,
                              Tristate.FALSE, Tristate.TRUE, (720, 1600))
    services = FakeDeviceServices(expected)
    assert services.snapshot(MonotonicSeconds(5.0)) is expected
    assert services.calls == 1
    services.close()
    assert services.closed


def test_replay_device_services_never_queries_external_state():
    services = ReplayDeviceServices()
    result = services.snapshot(MonotonicSeconds(7.0))
    assert result.foreground is Tristate.UNKNOWN
    assert result.keyboard is Tristate.UNKNOWN
    assert result.online is Tristate.UNKNOWN
    assert result.logical_wh is None
    assert services.external_calls == 0


def test_live_device_service_returns_unknown_when_cached_sample_expires():
    physical = [100.0]
    clock = SystemClock(lambda: physical[0], origin=100.0)
    services = LiveDeviceServices(clock, "game", autostart=False, max_age=3.0)
    fresh = DeviceSnapshot(MonotonicSeconds(0.0), Tristate.TRUE,
                           Tristate.FALSE, Tristate.TRUE, (720, 1600))
    services.publish(fresh)
    assert services.snapshot(MonotonicSeconds(2.0)) is fresh
    stale = services.snapshot(MonotonicSeconds(4.0))
    assert stale.foreground is Tristate.UNKNOWN
    assert stale.keyboard is Tristate.UNKNOWN
    assert stale.online is Tristate.UNKNOWN
    assert stale.logical_wh is None
    services.close()
