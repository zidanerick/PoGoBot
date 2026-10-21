import time

import cv2
import numpy as np
import pytest

from pogobot.capture import ReplaySource
from pogobot.config import DEFAULT
from pogobot.contracts import FrameRef, MonotonicSeconds, SystemClock
from pogobot.observation import ScreenGuess
from pogobot.perception import Perceptor
from pogobot.runner import Runner


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
