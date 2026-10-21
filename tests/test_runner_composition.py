"""Runner owns run identity and composes one clock and device-services seam."""

import subprocess
import time

from pogobot.config import DEFAULT
from pogobot.contracts import SystemClock
from pogobot.device import FakeDeviceServices, ReplayDeviceServices
from pogobot.runner import Runner


class _Actuator:
    dry_run = True
    serial = None

    def stats(self):
        return {"sent": 0}

    def close(self):
        pass


class _Source:
    identity_required = False
    is_replay = False

    def __init__(self):
        self.monotonic_origin = time.perf_counter()
        self.bound_run = None

    def bind_run(self, run_id, clock):
        self.bound_run = (run_id, clock)

    def release(self):
        pass


class _ReplaySource(_Source):
    is_replay = True


def test_runner_owns_and_injects_one_run_id_and_clock():
    source = _Source()
    clock = SystemClock(time.perf_counter, origin=source.monotonic_origin)
    services = FakeDeviceServices()
    runner = Runner(DEFAULT, source, _Actuator(), object(), display=False,
                    clock=clock, device_services=services)

    assert runner.run_id
    assert source.bound_run == (runner.run_id, clock)
    assert runner.clock is clock
    assert services.calls == 1
    runner.close()
    assert services.closed


def test_replay_runner_never_constructs_live_device_services(monkeypatch):
    def reject_process(*_args, **_kwargs):
        raise AssertionError("replay composition attempted external process access")

    monkeypatch.setattr(subprocess, "run", reject_process)
    runner = Runner(DEFAULT, _ReplaySource(), _Actuator(), object(), display=False)

    assert isinstance(runner.device_services, ReplayDeviceServices)
    snapshot = runner.device_services.snapshot(runner.clock.monotonic_now())
    assert snapshot.online.name == "UNKNOWN"
    assert runner.device_services.external_calls == 0
    runner.close()
