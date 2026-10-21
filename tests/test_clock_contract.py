from pogobot.contracts import ActiveSeconds, MonotonicSeconds, SystemClock


def test_active_clock_freezes_while_monotonic_clock_advances():
    physical = [100.0]
    clock = SystemClock(lambda: physical[0])

    physical[0] = 110.0
    assert clock.monotonic_now() == MonotonicSeconds(10.0)
    assert clock.active_now() == ActiveSeconds(10.0)

    clock.set_paused(True, at=clock.monotonic_now())
    physical[0] = 170.0
    assert clock.monotonic_now() == MonotonicSeconds(70.0)
    assert clock.active_now() == ActiveSeconds(10.0)

    clock.set_paused(False, at=clock.monotonic_now())
    physical[0] = 175.0
    assert clock.active_now() == ActiveSeconds(15.0)


def test_repeated_pause_state_is_idempotent():
    physical = [0.0]
    clock = SystemClock(lambda: physical[0])
    clock.set_paused(True, at=MonotonicSeconds(0.0))
    clock.set_paused(True, at=MonotonicSeconds(0.0))
    physical[0] = 4.0
    clock.set_paused(False, at=MonotonicSeconds(4.0))
    clock.set_paused(False, at=MonotonicSeconds(4.0))
    assert clock.active_now() == ActiveSeconds(0.0)


def test_pause_transitions_reject_a_regressing_physical_timestamp():
    clock = SystemClock(lambda: 0.0)
    clock.set_paused(True, at=MonotonicSeconds(3.0))
    try:
        clock.set_paused(False, at=MonotonicSeconds(2.0))
    except ValueError as exc:
        assert "nondecreasing" in str(exc)
    else:
        raise AssertionError("regressing pause timestamp was accepted")
