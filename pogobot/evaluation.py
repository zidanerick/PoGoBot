"""Offline evaluation primitives. No model, device, or runtime-policy dependencies."""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Box:
    label: str
    xyxy: tuple[float, float, float, float]
    confidence: float = 1.0

    def __post_init__(self):
        x0, y0, x1, y1 = self.xyxy
        if not self.label or not all(math.isfinite(v) for v in (*self.xyxy, self.confidence)):
            raise ValueError("box requires a class and finite coordinates/confidence")
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and 0 <= self.confidence <= 1):
            raise ValueError("box must have positive area inside normalized frame bounds")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iou(a: Box, b: Box) -> float:
    ax0, ay0, ax1, ay1 = a.xyxy
    bx0, by0, bx1, by1 = b.xyxy
    intersection = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(0.0, min(ay1, by1) - max(ay0, by0))
    return intersection / ((ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - intersection)


def read_labels(path: Path, classes: dict[str, str]) -> list[Box]:
    boxes = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5 or parts[0] not in classes:
            raise ValueError(f"invalid YOLO class/row in {path}: {line}")
        cx, cy, w, h = map(float, parts[1:])
        boxes.append(Box(classes[parts[0]], (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)))
    return boxes


def local_path(root: Path, value: str) -> Path:
    if not isinstance(value, str) or Path(value).is_absolute():
        raise ValueError("manifest paths must be relative")
    result = (root / value).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("manifest path escapes its directory")
    return result


def load_manifest(path: Path) -> dict:
    """Fail closed on unreviewed/changed labels or recording/image split leakage.

    A review asserts exhaustive detector annotations, including explicitly empty negative
    labels. Hashes bind that assertion to both files; model predictions are never truth.
    """
    doc = json.loads(path.read_text())
    if doc.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema_version")
    classes = doc.get("classes")
    if (not isinstance(classes, dict) or not classes
            or set(classes) != {str(i) for i in range(len(classes))}
            or any(not isinstance(v, str) or not v.strip() for v in classes.values())
            or len(set(classes.values())) != len(classes)):
        raise ValueError("classes must map consecutive string IDs to unique names")
    samples = doc.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("manifest must contain samples")
    recording_splits = doc.get("recording_splits")
    if recording_splits is not None:
        if (not isinstance(recording_splits, dict)
                or any(not isinstance(k, str) or not k.strip() for k in recording_splits)
                or any(v not in {"train", "validation", "test"} for v in recording_splits.values())):
            raise ValueError("recording_splits must map recording IDs to valid immutable splits")
    recordings, images, paths, ids = {}, {}, set(), set()
    for row in samples:
        sid, group, split = row.get("id"), row.get("recording_id"), row.get("split")
        if not isinstance(sid, str) or not sid.strip() or sid in ids:
            raise ValueError("sample IDs must be nonempty and unique")
        ids.add(sid)
        if not isinstance(group, str) or not group.strip() or split not in {"train", "validation", "test"}:
            raise ValueError(f"{sid}: recording_id and explicit split required")
        if group in recordings and recordings[group] != split:
            raise ValueError(f"recording split leakage: {group}")
        if recording_splits is not None and recording_splits.get(group) != split:
            raise ValueError(f"recording_splits disagrees with sample assignment: {group}")
        recordings[group] = split
        review = row.get("review", {})
        if (review.get("approved") is not True or review.get("exhaustive") is not True
                or not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()):
            raise ValueError(f"{sid}: exhaustive human review approval required")
        for field in ("image", "labels"):
            file = local_path(path.parent, row[field])
            if file in paths:
                raise ValueError(f"duplicate sample file: {file}")
            paths.add(file)
            digest = sha256(file)
            if digest != review.get(field + "_sha256"):
                raise ValueError(f"{sid}: {field} changed since review")
            if field == "image":
                if digest in images:
                    raise ValueError(f"duplicate image content: {sid} and {images[digest]}")
                images[digest] = sid
        read_labels(local_path(path.parent, row["labels"]), classes)
    return doc


def evaluate_frames(frames, classes, *, threshold=0.5) -> dict:
    """Class-aware, confidence-ordered greedy matching at one explicit IoU threshold.

    This is fixed-threshold precision/recall, NOT mAP or an optimal assignment score.
    A wrong class creates an FP for its predicted class and an FN for the true class.
    """
    if not 0 < threshold <= 1:
        raise ValueError("IoU threshold must be in (0, 1]")
    names = tuple(classes)
    if not names or len(set(names)) != len(names):
        raise ValueError("classes must be nonempty and unique")
    counts = {name: Counter(tp=0, fp=0, fn=0, support=0) for name in names}
    n = negatives = negative_fp_frames = negative_fp = 0
    for truth, predictions in frames:
        n += 1
        used = set()
        for box in (*truth, *predictions):
            if box.label not in counts:
                raise ValueError(f"unmapped class: {box.label}")
        if not truth:
            negatives += 1
            negative_fp_frames += bool(predictions)
            negative_fp += len(predictions)
        for box in truth:
            counts[box.label]["support"] += 1
        for pred in sorted(predictions, key=lambda b: b.confidence, reverse=True):
            candidates = [(iou(pred, gt), i) for i, gt in enumerate(truth)
                          if i not in used and gt.label == pred.label]
            overlap, index = max(candidates, default=(0.0, -1))
            if overlap >= threshold:
                used.add(index)
                counts[pred.label]["tp"] += 1
            else:
                counts[pred.label]["fp"] += 1
        for i, gt in enumerate(truth):
            if i not in used:
                counts[gt.label]["fn"] += 1
    if not n:
        raise ValueError("cannot evaluate an empty split")

    def metrics(c):
        tp, fp, fn = c["tp"], c["fp"], c["fn"]
        return {**c, "precision": tp / (tp + fp) if tp + fp else None,
                "recall": tp / (tp + fn) if tp + fn else None,
                "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}

    total = Counter(tp=0, fp=0, fn=0, support=0)
    for count in counts.values():
        total.update(count)
    return {"frames": n, "iou_threshold": threshold, "per_class": {k: metrics(v) for k, v in counts.items()},
            "micro": metrics(total), "negative_frames": negatives,
            "negative_false_positive_frames": negative_fp_frames,
            "negative_false_positive_boxes": negative_fp,
            "negative_frame_false_positive_rate": negative_fp_frames / negatives if negatives else None}

OUTCOMES = ("caught", "fled", "broke_out", "still_in_encounter", "out_of_balls")


def evaluate_outcomes(rows: list[dict]) -> dict:
    """Score one prediction per labelled interaction; UNKNOWN is an abstention.

    Rows represent action-linked episodes, not frames, so animations sampled at 30 FPS
    cannot inflate the denominator. Outcomes outside OUTCOMES are refused, not coerced.
    """
    labels = (*OUTCOMES, "unknown")
    matrix = {truth: Counter({pred: 0 for pred in labels}) for truth in OUTCOMES}
    seen_ids, groups = set(), {}
    unresolved_truth = 0
    for row in rows:
        action_id, recording = row.get("action_id"), row.get("recording_id")
        truth, prediction = row.get("truth"), row.get("prediction")
        if not isinstance(action_id, str) or not action_id.strip() or action_id in seen_ids:
            raise ValueError("outcome episodes require unique nonempty action_id values")
        seen_ids.add(action_id)
        if not isinstance(recording, str) or not recording.strip():
            raise ValueError(f"{action_id}: recording_id required")
        if truth not in labels or prediction not in labels:
            raise ValueError(f"{action_id}: invalid truth or prediction; use unknown when unresolved")
        if row.get("reviewed") is not True or not str(row.get("evidence", "")).strip():
            raise ValueError(f"{action_id}: reviewed label and evidence reference required")
        if truth == "unknown":
            # The reviewer could not establish ground truth from available evidence.
            # Keep the episode in corpus accounting, but never score it as a class.
            unresolved_truth += 1
        else:
            matrix[truth][prediction] += 1
        groups[recording] = groups.get(recording, 0) + 1
    if not rows:
        raise ValueError("cannot evaluate an empty outcome set")
    n = len(rows)
    resolved = n - unresolved_truth
    covered = sum(matrix[t][p] for t in OUTCOMES for p in OUTCOMES)
    correct = sum(matrix[t][t] for t in OUTCOMES)
    per_class = {}
    for label in OUTCOMES:
        tp = matrix[label][label]
        fp = sum(matrix[t][label] for t in OUTCOMES if t != label)
        fn = sum(matrix[label][p] for p in labels if p != label)
        per_class[label] = {"support": sum(matrix[label].values()), "tp": tp, "fp": fp, "fn": fn,
                            "precision": tp / (tp + fp) if tp + fp else None,
                            "recall": tp / (tp + fn) if tp + fn else None}
    prediction_coverage = covered / resolved if resolved else None
    return {"episodes": n, "recordings": len(groups),
            "truth_resolved_episodes": resolved, "unresolved_truth_episodes": unresolved_truth,
            "truth_resolution_rate": resolved / n,
            "coverage": prediction_coverage, "prediction_coverage": prediction_coverage,
            "covered_accuracy": correct / covered if covered else None,
            "accuracy_over_resolved_episodes": correct / resolved if resolved else None,
            "abstentions": resolved - covered, "confusion": {k: dict(v) for k, v in matrix.items()},
            "per_class": per_class}


def bootstrap_outcome_intervals(rows: list[dict], *, resamples: int = 2000,
                                confidence: float = 0.95, seed: int = 0) -> dict:
    """Estimate uncertainty by resampling whole recordings, not correlated episodes.

    The percentile intervals describe between-recording sampling variation. They are
    not a substitute for a held-out test set, and are withheld for fewer than two
    independent recordings.
    """
    if not isinstance(resamples, int) or resamples < 1:
        raise ValueError("resamples must be a positive integer")
    if not math.isfinite(confidence) or not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    evaluate_outcomes(rows)  # validate reviewed labels and unique action IDs first
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["recording_id"], []).append(row)
    names = ("coverage", "covered_accuracy", "accuracy_over_resolved_episodes")
    values = {name: [] for name in names}
    for label in OUTCOMES:
        values[f"per_class.{label}.precision"] = []
        values[f"per_class.{label}.recall"] = []
    if len(groups) < 2:
        return {"method": "recording_cluster_percentile_bootstrap",
                "confidence_level": confidence, "requested_resamples": resamples,
                "seed": seed, "independent_recordings": len(groups), "intervals": None,
                "note": "need at least two independent recordings for an interval"}

    recording_ids = list(groups)
    rng = random.Random(seed)
    for iteration in range(resamples):
        sampled = rng.choices(recording_ids, k=len(recording_ids))
        replicate = []
        for draw, recording_id in enumerate(sampled):
            for row in groups[recording_id]:
                replicate.append({**row,
                                  "action_id": f"{row['action_id']}#bootstrap-{iteration}-{draw}",
                                  "recording_id": f"{recording_id}#bootstrap-{draw}"})
        metrics = evaluate_outcomes(replicate)
        for name in names:
            values[name].append(metrics[name])
        for label in OUTCOMES:
            for metric in ("precision", "recall"):
                values[f"per_class.{label}.{metric}"].append(metrics["per_class"][label][metric])

    alpha = (1 - confidence) / 2

    def quantile(data: list[float], probability: float) -> float:
        ordered = sorted(data)
        position = (len(ordered) - 1) * probability
        low = math.floor(position)
        high = math.ceil(position)
        return ordered[low] + (ordered[high] - ordered[low]) * (position - low)

    intervals = {}
    for name, estimates in values.items():
        valid = [value for value in estimates if value is not None]
        intervals[name] = ({"lower": quantile(valid, alpha),
                            "upper": quantile(valid, 1 - alpha),
                            "valid_resamples": len(valid)} if valid else None)
    return {"method": "recording_cluster_percentile_bootstrap",
            "confidence_level": confidence, "requested_resamples": resamples,
            "seed": seed, "independent_recordings": len(groups), "intervals": intervals,
            "note": "approximate between-recording uncertainty; use a pre-held-out test split"}
