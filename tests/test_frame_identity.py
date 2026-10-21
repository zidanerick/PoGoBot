import time
import threading

import cv2
import numpy as np
import pytest

from pogobot.capture import ReplaySource, ScrcpySource
from pogobot.config import DEFAULT
from pogobot.contracts import FrameRef, MonotonicSeconds, SystemClock
from pogobot.effects import BotState, IntentOutcome
from pogobot.fsm import Intent
from pogobot.learning import IntentLedger
from pogobot.observation import ScreenGuess
from pogobot.perception import Perceptor
from pogobot.runner import Runner
from tests.factories import det, obs as make_obs


class _Actuator:
    dry_run = True
    serial = None

    def stats(self):
        return {"sent": 0}

    def close(self):
        pass


def test_replay_stamps_frame_identity_and_perception_copies_it(tmp_path, monkeypatch):
    image = np.zeros((128, 64, 3), dtype=np.uint8)
    assert cv2.imwrite(str(tmp_path / "frame.png"), image)
    source = ReplaySource(tmp_path)
    clock = SystemClock(time.perf_counter, origin=source.monotonic_origin)
    source.bind_run("runner-owned-run", clock)
    frame = source.read()
    assert frame is not None
    assert isinstance(frame.frame_ref, FrameRef)
    assert frame.frame_ref.run_id == "runner-owned-run"
    assert frame.frame_ref.capture_epoch == 0
    assert frame.frame_ref.seq == frame.seq
    assert frame.frame_ref.captured_mono_s == MonotonicSeconds(frame.ts)

    perceptor = Perceptor(DEFAULT)
    monkeypatch.setattr(
        perceptor, "classify_screen",
        lambda _bgr: ScreenGuess("unknown", 0.0, available=False),
    )
    observation = perceptor.observe(frame, run_detector=False)
    assert observation.frame_ref is frame.frame_ref
    assert observation.seq == frame.seq
    assert observation.ts == frame.ts
    source.release()


def test_replay_seek_advances_epoch_and_restarts_sequence(tmp_path):
    for name in ("1.png", "2.png"):
        assert cv2.imwrite(str(tmp_path / name), np.zeros((32, 24, 3), dtype=np.uint8))
    source = ReplaySource(tmp_path)
    clock = SystemClock(time.perf_counter, origin=source.monotonic_origin)
    source.bind_run("run", clock)
    before = source.read()
    assert before is not None and before.frame_ref is not None
    source.seek(0)
    after = source.read()
    assert after is not None and after.frame_ref is not None
    assert after.seq == 1
    assert after.frame_ref.capture_epoch == before.frame_ref.capture_epoch + 1
    assert after.frame_ref.run_id == before.frame_ref.run_id


def test_replay_reset_advances_epoch_and_restarts_sequence(tmp_path):
    for name in ("1.png", "2.png"):
        assert cv2.imwrite(str(tmp_path / name), np.zeros((32, 24, 3), dtype=np.uint8))
    source = ReplaySource(tmp_path)
    clock = SystemClock(time.perf_counter, origin=source.monotonic_origin)
    source.bind_run("run", clock)
    before = source.read()
    assert before is not None and before.frame_ref is not None
    epoch = source.reset()
    after = source.read()
    assert after is not None and after.frame_ref is not None
    assert epoch == before.frame_ref.capture_epoch + 1
    assert after.seq == 1
    assert after.frame_ref.capture_epoch == epoch


def test_live_reset_and_reconnect_advance_epoch_and_drop_buffered_frame():
    # Exercise the epoch lifecycle without starting scrcpy or opening a device.
    source = ScrcpySource.__new__(ScrcpySource)
    source._lock = threading.Lock()
    source._capture_epoch = 0
    source._seq = 8
    source._latest = np.zeros((4, 4, 3), dtype=np.uint8)
    source._latest_seq = 8
    source._latest_ts = 12.0
    source._latest_epoch = 0

    assert source.reset() == 1
    assert source._seq == 0 and source._latest is None
    source._seq = 3
    source._latest = np.zeros((4, 4, 3), dtype=np.uint8)
    source._latest_seq = 3
    assert source.reconnected() == 2
    assert source._seq == 0 and source._latest is None


def test_delayed_old_epoch_intent_cannot_resolve_reused_sequence(tmp_path):
    old_ref = FrameRef("run", 0, 1, MonotonicSeconds(1.0))
    new_ref = FrameRef("run", 1, 1, MonotonicSeconds(1.2))
    from pogobot.frames import Frame
    new_frame = Frame(seq=1, ts=1.2, bgr=np.zeros((32, 24, 3), dtype=np.uint8),
                      frame_ref=new_ref)
    observation = make_obs(seq=1, ts=1.2, detections=(det(),))
    from dataclasses import replace
    observation = replace(observation, frame_ref=new_ref)
    ledger = IntentLedger(tmp_path / "ledger", ["pokemon"])
    try:
        ledger.stage(new_frame, observation)
        old_intent = Intent(
            ts=1.0, target_name="pokemon", confidence=0.9,
            tap_norm=(0.5, 0.6), xywhn=(0.5, 0.6, 0.1, 0.1),
            expected=BotState.ENCOUNTER, frame_seq=1, frame_ref=old_ref,
        )
        reason = ledger.resolve(old_intent, IntentOutcome.CONFIRMED, now=1.5)
        assert reason == "tap frame no longer staged"
    finally:
        ledger.close()


def test_runner_owns_run_id_and_rejects_mismatched_production_identity(tmp_path):
    assert cv2.imwrite(str(tmp_path / "frame.png"), np.zeros((32, 24, 3), dtype=np.uint8))
    source = ReplaySource(tmp_path)
    clock = SystemClock(time.perf_counter, origin=source.monotonic_origin)
    runner = Runner(DEFAULT, source, _Actuator(), object(), display=False, clock=clock)
    frame = source.read()
    assert frame is not None and frame.frame_ref is not None
    assert frame.frame_ref.run_id == runner.run_id
    runner._validate_frame_identity(frame)

    bad_ref = FrameRef("other-run", frame.frame_ref.capture_epoch, frame.seq,
                       frame.frame_ref.captured_mono_s)
    from dataclasses import replace
    with pytest.raises(ValueError, match="mismatched identity"):
        runner._validate_frame_identity(replace(frame, frame_ref=bad_ref))
    with pytest.raises(ValueError, match="missing or mismatched identity"):
        runner._validate_frame_identity(replace(frame, frame_ref=None))
    runner.close()
