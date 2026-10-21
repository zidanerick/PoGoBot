from threading import Event
from time import perf_counter, sleep

import pytest

from pogobot import device
from pogobot.observation import Tristate


def test_foreground_query_distinguishes_game_foreign_and_unavailable(monkeypatch):
    class Result:
        returncode = 0
        stdout = (b"mCurrentFocus=Window{abcdef u0 com.example.game/.MainActivity}\n")
    monkeypatch.setattr(device.subprocess, "run", lambda *a, **kw: Result())
    assert device.foreground_state("com.example.game") is Tristate.TRUE
    assert device.foreground_state("com.example") is Tristate.FALSE

    class Failed(Result):
        returncode = 1
    monkeypatch.setattr(device.subprocess, "run", lambda *a, **kw: Failed())
    assert device.foreground_state("com.example.game") is Tristate.UNKNOWN


def test_foreground_poller_returns_immediately_and_publishes_result():
    entered, release = Event(), Event()
    def query(*_):
        entered.set()
        release.wait(2)
        return Tristate.FALSE

    poller = device.ForegroundPoller(query=query, interval=60)
    started = perf_counter()
    assert poller.read("com.example.game") is Tristate.UNKNOWN
    assert perf_counter() - started < 0.1
    assert entered.wait(0.5)
    release.set()
    for _ in range(100):
        if poller.read("com.example.game") is Tristate.FALSE:
            break
        sleep(0.005)
    assert poller.read("com.example.game") is Tristate.FALSE
    poller.close()


def test_invalidation_discards_a_result_from_an_old_screen():
    release = Event()
    poller = device.ForegroundPoller(
        query=lambda *_: (release.wait(2), Tristate.FALSE)[1], interval=60)
    assert poller.read("com.example.game") is Tristate.UNKNOWN
    poller.invalidate()
    release.set()
    for _ in range(100):
        if not poller._running:
            break
        sleep(0.005)
    assert poller.read("com.example.game") is Tristate.UNKNOWN
    poller.close()


def test_query_exception_remains_unknown():
    poller = device.ForegroundPoller(query=lambda *_: (_ for _ in ()).throw(TimeoutError()),
                                     interval=60)
    assert poller.read("com.example.game") is Tristate.UNKNOWN
    for _ in range(100):
        if not poller._running:
            break
        sleep(0.005)
    assert poller.read("com.example.game") is Tristate.UNKNOWN
    poller.close()


def test_close_invalidates_and_disables_future_queries():
    calls = []
    poller = device.ForegroundPoller(query=lambda *_: calls.append(1) or True)
    poller.close()
    assert poller.read("com.example.game") is Tristate.UNKNOWN
    assert calls == []
