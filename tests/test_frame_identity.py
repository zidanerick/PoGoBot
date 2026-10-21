import cv2
import numpy as np

from pogobot.capture import ReplaySource
from pogobot.config import DEFAULT
from pogobot.contracts import FrameRef, MonotonicSeconds
from pogobot.observation import ScreenGuess
from pogobot.perception import Perceptor


def test_replay_stamps_frame_identity_and_perception_copies_it(tmp_path, monkeypatch):
    image = np.zeros((128, 64, 3), dtype=np.uint8)
    assert cv2.imwrite(str(tmp_path / "frame.png"), image)
    source = ReplaySource(tmp_path)
    frame = source.read()
    assert frame is not None
    assert isinstance(frame.frame_ref, FrameRef)
    assert frame.frame_ref.run_id
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
