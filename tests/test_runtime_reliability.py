"""Public-path regressions for recovery and empty-map camera rotation."""
from dataclasses import replace

import numpy as np
import pytest

from pogobot import fsm, runner as R
from pogobot.config import DEFAULT
from pogobot.effects import (BotState, Back, DoubleTapDrag, Pinch, Tap, Swipe,
                             RestartApp, Halt, ForegroundApp)
from pogobot.frames import Frame
from pogobot.observation import Tristate
from tests.factories import obs
from tests.test_switch_runner import make_runner


@pytest.mark.parametrize('spent,expected', [(0, RestartApp), (DEFAULT.max_app_restarts, Halt)])
def test_foreign_foreground_reaches_escalation_through_dispatch(spent, expected):
    c = fsm.Context(cfg=DEFAULT, state=BotState.RECOVERING, now=500,
                    state_since=0, last_map_ts=0, app_foreground=Tristate.FALSE,
                    foregrounds=DEFAULT.max_foreground_attempts, app_restarts=spent)
    effects = fsm.step(obs(screen='Menu'), c)
    assert any(isinstance(e, expected) for e in effects)
    assert not any(isinstance(e, (Tap, Back, Swipe)) for e in effects)


def test_foreign_foreground_refuses_interrupt_gestures_between_raise_attempts():
    c = fsm.Context(cfg=DEFAULT, state=BotState.RECOVERING, now=100,
                    state_since=99, app_foreground=Tristate.FALSE)
    c.last_action['foreground'] = 100
    assert not any(isinstance(e, (Back, Tap, Swipe))
                   for e in fsm.step(obs(screen='Menu', keyboard=Tristate.TRUE), c))


def test_pending_foreground_poll_keeps_known_foreign_screen_blocked():
    class PendingService:
        def read(self, package): return Tristate.UNKNOWN
        def invalidate(self): pass
        def close(self): pass

    r = make_runner(device_service=PendingService())
    r.ctx.state = BotState.RECOVERING
    r.ctx.now = 100.0
    r.ctx.app_foreground = Tristate.FALSE
    raise_app = ForegroundApp(DEFAULT.app_package, DEFAULT.app_activity, "raise")
    r.apply([raise_app], obs(screen="Menu"))
    r._refresh_foreground(101.0, False)
    assert r.ctx.app_foreground is Tristate.FALSE
    effects = fsm.step(obs(screen="Menu", keyboard=Tristate.TRUE, ts=101.0), r.ctx)
    assert not any(isinstance(e, (Back, Tap, Swipe)) for e in effects)


def test_empty_map_rotates_through_runner_without_aging_recovery_clock(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(R.time, 'perf_counter', lambda: clock[0])
    timings = replace(DEFAULT.timings, scanning_idle_rotate=3, rotate_camera=1)
    r = make_runner(DEFAULT.scaled(timings=timings, preflight=False, auto_rotate=True))
    r.ctx.state = BotState.SCANNING

    class Source:
        seq = 0
        def healthy(self):
            return self.seq < 12
        def read(self):
            self.seq += 1
            clock[0] += 1
            return Frame(self.seq, clock[0], np.zeros((20, 20, 3), dtype=np.uint8))
        def release(self):
            pass

    class Perceptor:
        def observe(self, frame, **kw):
            return obs(seq=frame.seq, ts=frame.ts)

    r.source, r.perceptor = Source(), Perceptor()
    assert r.run() == 0
    rotations = [e for e in r.actuator.applied if isinstance(e, Swipe) and e.budget == 'rotate']
    assert 2 <= len(rotations) <= 4
    assert r.ctx.last_map_ts >= clock[0] - 1


@pytest.mark.parametrize("accepted", [False, True])
def test_rotation_clears_spatial_memory_only_after_accepted_swipe(accepted):
    timings = replace(DEFAULT.timings, scanning_idle_rotate=1, rotate_camera=0)
    cfg = DEFAULT.scaled(auto_rotate=True, timings=timings)
    r = make_runner(cfg)
    r.ctx.state = BotState.SCANNING
    r.ctx.now = 100.0
    r.ctx.state_since = 0.0
    r.ctx.last_scan_progress_ts = 0.0
    r.ctx.cooldowns.append((0.4, 0.5, 200.0))
    if not accepted:
        r.actuator.apply = lambda *_args, **_kwargs: False
    observation = obs(ts=100.0)
    effects = fsm.step(observation, r.ctx)
    assert any(isinstance(e, Swipe) and e.budget == "rotate" for e in effects)
    r.apply(effects, observation)
    if accepted:
        assert r.ctx.cooldowns == []
        assert r.ctx.last_scan_progress_ts == 100.0
    else:
        assert r.ctx.cooldowns == [(0.4, 0.5, 200.0)]
        assert r.ctx.last_scan_progress_ts == 0.0


def test_blocked_foreground_query_never_blocks_the_runner_read():
    from threading import Event
    from time import perf_counter
    from pogobot.device import ForegroundPoller

    entered, release = Event(), Event()
    def slow_query(*_):
        entered.set()
        release.wait(2)
        return Tristate.FALSE

    poller = ForegroundPoller(query=slow_query, interval=60)
    started = perf_counter()
    assert poller.read(DEFAULT.app_package) is Tristate.UNKNOWN
    assert perf_counter() - started < 0.1
    assert entered.wait(0.5)
    release.set()
    for _ in range(100):
        if poller.read(DEFAULT.app_package) is Tristate.FALSE:
            break
        import time
        time.sleep(0.005)
    assert poller.read(DEFAULT.app_package) is Tristate.FALSE
    poller.close()


def test_failed_foreground_query_is_unknown_and_old_result_is_discarded():
    from threading import Event
    from pogobot.device import ForegroundPoller

    release = Event()
    poller = ForegroundPoller(query=lambda *_: (release.wait(1), Tristate.FALSE)[1],
                              interval=60)
    assert poller.read(DEFAULT.app_package) is Tristate.UNKNOWN
    poller.invalidate()
    release.set()
    import time
    for _ in range(100):
        if not poller._running:
            break
        time.sleep(0.005)
    assert poller.read(DEFAULT.app_package) is Tristate.UNKNOWN
    poller.close()


def test_expired_foreground_result_becomes_unknown_before_refresh():
    from pogobot.device import ForegroundPoller

    clock = [0.0]
    poller = ForegroundPoller(interval=5.0, query=lambda *_: Tristate.TRUE,
                              clock=lambda: clock[0])
    assert poller.read(DEFAULT.app_package) is Tristate.UNKNOWN
    for _ in range(100):
        if not poller._running:
            break
        import time
        time.sleep(0.002)
    assert poller.read(DEFAULT.app_package) is Tristate.TRUE

    clock[0] = 5.0
    assert poller.read(DEFAULT.app_package) is Tristate.UNKNOWN, \
        "an expired TRUE must not authorize input while the next query is pending"
    poller.close()


def test_runner_blocks_all_screen_input_when_foreground_is_unknown():
    r = make_runner()
    r.ctx.app_foreground = Tristate.UNKNOWN
    effects = [
        Back("unknown foreground"),
        Tap(0.2, 0.2, "unknown foreground"),
        Swipe(0.1, 0.2, 0.8, 0.2, "unknown foreground"),
        DoubleTapDrag(0.5, 0.5, 0.5, 0.3, "unknown foreground"),
        Pinch(0.5, 0.5, 0.4, 0.2, "unknown foreground"),
    ]

    r.apply(effects, obs(screen="Menu"))

    assert r.actuator.applied == []


def test_replay_and_unit_default_never_query_a_device():
    from pogobot.device import NullDeviceService
    r = make_runner()
    assert isinstance(r.device_service, NullDeviceService)
    r._refresh_foreground(1.0, False)
    assert r.ctx.app_foreground is Tristate.UNKNOWN


def test_slow_account_tree_query_does_not_block_recovery_ticks():
    from threading import Event
    from time import monotonic, sleep
    from pogobot.accounts import AccountView, AsyncUiTreeReader

    entered, release = Event(), Event()
    expected = AccountView(available=True, panel_open=True)

    class SlowReader:
        def read(self):
            entered.set()
            release.wait(1)
            return expected

    r = make_runner()
    r.ctx.state = BotState.RECOVERING
    r.tree_reader = AsyncUiTreeReader(SlowReader())
    started = monotonic()
    r._refresh_accounts(100.0)
    assert monotonic() - started < 0.1
    assert entered.wait(0.5)
    assert r.ctx.accounts is None

    release.set()
    for _ in range(100):
        if not r.tree_reader._running:
            break
        sleep(0.005)
    r._refresh_accounts(100.1)
    assert r.ctx.accounts is expected
    r.tree_reader.close()


def test_recovery_gesture_invalidates_a_completed_but_unconsumed_tree_snapshot():
    from time import sleep
    from pogobot.accounts import AccountView, AsyncUiTreeReader

    expected = AccountView(available=True, panel_open=True)

    class Reader:
        def read(self):
            return expected

    r = make_runner()
    r.ctx.state = BotState.RECOVERING
    r.tree_reader = AsyncUiTreeReader(Reader())
    r._refresh_accounts(100.0)
    for _ in range(100):
        if not r.tree_reader._running:
            break
        sleep(0.005)
    assert r.tree_reader._result is expected

    r.apply([Tap(0.2, 0.2, "close recovery panel",
                 budget="recover_panel_close")], obs())
    assert r.tree_reader.poll() is None
    assert r.ctx.accounts is None
    r.tree_reader.close()
