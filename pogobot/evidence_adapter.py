"""Narrow bridge from legacy perception values to the proposed T01 Evidence type.

T01's public contracts are not implemented on this branch yet. Keep their concrete
types injected here so this adapter can be tested now without defining a second
FrameRef/Evidence contract or editing lead-owned runtime interfaces.
"""
from __future__ import annotations

import math
from typing import Any


class EvidenceAdapter:
    """Adapt one legacy Observation fact while preserving its capture FrameRef.

    ``evidence_type`` and ``tristate_type`` should be the accepted T01 contract types
    once integrated. A FrameRef is supplied by the capture boundary; this adapter never
    invents or restamps run, epoch, sequence, or timestamp identity.
    """

    def __init__(self, evidence_type: type, tristate_type: type):
        self._evidence_type = evidence_type
        self._tristate_type = tristate_type

    @staticmethod
    def _validate_identity(frame: Any, observation: Any, frame_ref: Any) -> None:
        required = ("run_id", "capture_epoch", "seq", "captured_mono_s")
        if any(not hasattr(frame_ref, name) for name in required):
            raise ValueError("FrameRef is missing a required T01 identity field")
        if not isinstance(frame_ref.run_id, str) or not frame_ref.run_id.strip():
            raise ValueError("FrameRef.run_id must be nonempty")
        if type(frame_ref.capture_epoch) is not int or frame_ref.capture_epoch < 0:
            raise ValueError("FrameRef.capture_epoch must be a nonnegative integer")
        if type(frame_ref.seq) is not int or frame_ref.seq < 0:
            raise ValueError("FrameRef.seq must be a nonnegative integer")
        if (not isinstance(frame_ref.captured_mono_s, (int, float))
                or not math.isfinite(frame_ref.captured_mono_s)
                or frame_ref.captured_mono_s < 0):
            raise ValueError("FrameRef.captured_mono_s must be finite and nonnegative")
        if frame.seq != observation.seq or frame.ts != observation.ts:
            raise ValueError("Frame and Observation identities disagree")
        if frame_ref.seq != frame.seq or frame_ref.captured_mono_s != frame.ts:
            raise ValueError("FrameRef does not identify the source Frame")
        observation_ref = getattr(observation, "frame_ref", None)
        if observation_ref is not None and observation_ref != frame_ref:
            raise ValueError("Observation carries a different FrameRef")

    @staticmethod
    def _text(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be nonempty")
        return value

    def signal(self, *, frame: Any, observation: Any, frame_ref: Any,
               signal_name: str, kind: str, source: str) -> Any:
        """Convert a legacy boolean Signal to T01 Evidence.

        Legacy Signal.score is an optical measurement, not a calibrated probability,
        so it is intentionally not copied into ``confidence``.
        """
        self._validate_identity(frame, observation, frame_ref)
        signal_name = self._text(signal_name, "signal_name")
        kind = self._text(kind, "kind")
        source = self._text(source, "source")
        signal = getattr(observation, signal_name, None)
        if signal is None or not isinstance(getattr(signal, "value", None), bool):
            raise ValueError(f"Observation.{signal_name} is not a boolean Signal")
        value = self._tristate_type.TRUE if signal.value else self._tristate_type.FALSE
        return self._evidence_type(frame_ref=frame_ref, kind=kind, value=value,
                                   confidence=None, source=source,
                                   region_xyxyn=None)

    def detection(self, *, frame: Any, observation: Any, frame_ref: Any,
                  detection: Any, source: str) -> Any:
        """Convert a positive legacy Detection to normalized T01 Evidence.

        Absence is never emitted from an empty detector result. The reported detector
        score is copied as the contract's optional confidence field, without claiming
        that it is calibrated.
        """
        self._validate_identity(frame, observation, frame_ref)
        source = self._text(source, "source")
        if detection not in observation.detections:
            raise ValueError("Detection does not belong to the source Observation")
        kind = self._text(detection.name, "detection.name")
        confidence = detection.conf
        if (not isinstance(confidence, (int, float)) or not math.isfinite(confidence)
                or not 0 <= confidence <= 1):
            raise ValueError("detection confidence must be finite and between 0 and 1")
        if (len(detection.xywhn) != 4
                or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                       for v in detection.xywhn)):
            raise ValueError("detection xywhn must contain four finite normalized values")
        cx, cy, width, height = detection.xywhn
        region = (cx - width / 2, cy - height / 2,
                  cx + width / 2, cy + height / 2)
        x0, y0, x1, y1 = region
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise ValueError("detection region must have positive area inside normalized bounds")
        return self._evidence_type(frame_ref=frame_ref, kind=kind,
                                   value=self._tristate_type.TRUE,
                                   confidence=float(confidence), source=source,
                                   region_xyxyn=region)
