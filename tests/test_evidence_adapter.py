"""Offline adapter tests using local T01-shaped stand-ins, not shared runtime types."""

from dataclasses import dataclass
from enum import Enum
import pytest

from pogobot.evidence_adapter import EvidenceAdapter
from pogobot.frames import Frame
from pogobot.observation import Detection, Observation, ScreenGuess, Signal, Tristate


class T01Tristate(Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class FrameRef:
    run_id: str
    capture_epoch: int
    seq: int
    captured_mono_s: float


@dataclass(frozen=True)
class Evidence:
    frame_ref: FrameRef
    kind: str
    value: T01Tristate
    confidence: float | None
    source: str
    region_xyxyn: tuple[float, float, float, float] | None


def _inputs(*, seq=7, ts=12.5, frame_ref=None, detections=()):
    frame = Frame(seq=seq, ts=ts, bgr=None)
    observation = Observation(
        seq=seq,
        ts=ts,
        stream_wh=(100, 200),
        x_button=Signal(False, 0.0, 0.5),
        map_ball=Signal(True, 0.7, 0.5),
        encounter=Signal(False, 0.1, 0.5),
        claim_pill=Signal(False, 0.0, 0.5),
        stop_out_of_range=Signal(False, 0.0, 0.5),
        screen=ScreenGuess("Overworld", 0.9),
        detections=tuple(detections),
    )
    if frame_ref is not None:
        object.__setattr__(observation, "frame_ref", frame_ref)
    return frame, observation


@pytest.fixture
def adapter():
    return EvidenceAdapter(Evidence, T01Tristate)


def test_signal_adapts_boolean_without_treating_optical_score_as_confidence(adapter):
    frame_ref = FrameRef("run-1", 2, 7, 12.5)
    frame, observation = _inputs(frame_ref=frame_ref)

    evidence = adapter.signal(
        frame=frame,
        observation=observation,
        frame_ref=frame_ref,
        signal_name="map_ball",
        kind="map_ball_visible",
        source="legacy-optical",
    )

    assert evidence == Evidence(frame_ref, "map_ball_visible", T01Tristate.TRUE,
                                None, "legacy-optical", None)
    assert evidence.frame_ref is frame_ref


def test_adapter_does_not_compare_run_relative_ref_time_to_legacy_frame_time(adapter):
    frame_ref = FrameRef("run-1", 2, 7, 12.5)
    frame, observation = _inputs(ts=9000.5, seq=7)

    evidence = adapter.signal(
        frame=frame,
        observation=observation,
        frame_ref=frame_ref,
        signal_name="map_ball",
        kind="map_ball_visible",
        source="legacy-optical",
    )

    assert evidence.frame_ref is frame_ref


def test_adapter_rejects_missing_frame_bound_reference_when_type_exposes_field(adapter):
    frame_ref = FrameRef("run-1", 2, 7, 12.5)
    frame, observation = _inputs(frame_ref=frame_ref)
    object.__setattr__(frame, "frame_ref", None)

    with pytest.raises(ValueError, match="Frame is missing"):
        adapter.signal(frame=frame, observation=observation, frame_ref=frame_ref,
                       signal_name="map_ball", kind="map_ball_visible", source="optical")


def test_adapter_rejects_frame_bound_to_a_different_run_or_epoch(adapter):
    frame_ref = FrameRef("run-1", 2, 7, 12.5)
    frame, observation = _inputs(frame_ref=frame_ref)
    object.__setattr__(frame, "frame_ref", FrameRef("other-run", 3, 7, 12.5))

    with pytest.raises(ValueError, match="different FrameRef"):
        adapter.signal(frame=frame, observation=observation, frame_ref=frame_ref,
                       signal_name="map_ball", kind="map_ball_visible", source="optical")


def test_adapter_rejects_observation_missing_bound_reference_when_type_exposes_field(adapter):
    frame_ref = FrameRef("run-1", 2, 7, 12.5)
    frame, observation = _inputs(frame_ref=frame_ref)
    object.__setattr__(observation, "frame_ref", None)

    with pytest.raises(ValueError, match="Observation is missing"):
        adapter.signal(frame=frame, observation=observation, frame_ref=frame_ref,
                       signal_name="map_ball", kind="map_ball_visible", source="optical")


def test_detection_adapts_normalized_region_and_preserves_exact_frame_ref(adapter):
    detection = Detection("pokemon", 0.8, (25, 50, 75, 150), (0.5, 0.5, 0.5, 0.5))
    frame_ref = FrameRef("run-1", 2, 7, 12.5)
    frame, observation = _inputs(frame_ref=frame_ref, detections=(detection,))

    evidence = adapter.detection(
        frame=frame,
        observation=observation,
        frame_ref=frame_ref,
        detection=detection,
        source="detector-v1",
    )

    assert evidence == Evidence(frame_ref, "pokemon", T01Tristate.TRUE, 0.8,
                                "detector-v1", (0.25, 0.25, 0.75, 0.75))
    assert evidence.frame_ref is frame_ref


def test_tristate_preserves_unknown_instead_of_turning_it_into_false(adapter):
    frame_ref = FrameRef("run-1", 2, 7, 12.5)
    frame, observation = _inputs(frame_ref=frame_ref)
    object.__setattr__(observation, "goplus", Tristate.UNKNOWN)

    evidence = adapter.tristate(
        frame=frame,
        observation=observation,
        frame_ref=frame_ref,
        field_name="goplus",
        kind="goplus_enabled",
        source="legacy-screen-state",
    )

    assert evidence.value is T01Tristate.UNKNOWN
    assert evidence.frame_ref is frame_ref
    assert evidence.confidence is None


@pytest.mark.parametrize(
    "frame_seq,frame_ts,observation_seq,observation_ts,ref_seq,ref_ts",
    [
        (7, 12.5, 8, 12.5, 7, 12.5),
        (7, 12.5, 7, 12.6, 7, 12.5),
        (7, 12.5, 7, 12.5, 8, 12.5),
    ],
)
def test_signal_rejects_frame_observation_or_frame_ref_identity_mismatch(
    adapter, frame_seq, frame_ts, observation_seq, observation_ts, ref_seq, ref_ts
):
    frame = Frame(seq=frame_seq, ts=frame_ts, bgr=None)
    observation = _inputs(seq=observation_seq, ts=observation_ts)[1]
    frame_ref = FrameRef("run-1", 2, ref_seq, ref_ts)

    with pytest.raises(ValueError, match="identit|identify"):
        adapter.signal(frame=frame, observation=observation, frame_ref=frame_ref,
                       signal_name="map_ball", kind="map_ball_visible", source="optical")


def test_signal_rejects_nonfinite_t01_capture_timestamp(adapter):
    frame, observation = _inputs()
    frame_ref = FrameRef("run-1", 2, 7, float("nan"))

    with pytest.raises(ValueError, match="finite"):
        adapter.signal(frame=frame, observation=observation, frame_ref=frame_ref,
                       signal_name="map_ball", kind="map_ball_visible", source="optical")


def test_rejects_observation_bound_to_a_different_frame_ref(adapter):
    frame_ref = FrameRef("run-1", 2, 7, 12.5)
    frame, observation = _inputs(frame_ref=FrameRef("run-1", 2, 6, 12.4))

    with pytest.raises(ValueError, match="different FrameRef"):
        adapter.signal(frame=frame, observation=observation, frame_ref=frame_ref,
                       signal_name="map_ball", kind="map_ball_visible", source="optical")


@pytest.mark.parametrize(
    "xywhn",
    [
        (0.5, 0.5, 0.5, float("nan")),
        (0.5, 0.5, 1.1, 0.5),
        (0.0, 0.5, 0.2, 0.2),
    ],
)
def test_detection_rejects_nonfinite_or_out_of_bounds_regions(adapter, xywhn):
    detection = Detection("pokemon", 0.8, (0, 0, 1, 1), xywhn)
    frame_ref = FrameRef("run-1", 2, 7, 12.5)
    frame, observation = _inputs(frame_ref=frame_ref, detections=(detection,))

    with pytest.raises(ValueError, match="finite|bounds"):
        adapter.detection(frame=frame, observation=observation, frame_ref=frame_ref,
                          detection=detection, source="detector-v1")


def test_detection_rejects_box_from_another_observation(adapter):
    detection = Detection("pokemon", 0.8, (0, 0, 10, 10), (0.5, 0.5, 0.1, 0.1))
    frame_ref = FrameRef("run-1", 2, 7, 12.5)
    frame, observation = _inputs(frame_ref=frame_ref)

    with pytest.raises(ValueError, match="does not belong"):
        adapter.detection(frame=frame, observation=observation, frame_ref=frame_ref,
                          detection=detection, source="detector-v1")
