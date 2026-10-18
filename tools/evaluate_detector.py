#!/usr/bin/env python3
"""Evaluate one detector on a reviewed manifest. Never installs or modifies weights."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pogobot.evaluation import Box, evaluate_frames, load_manifest, local_path, read_labels, sha256


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--weights", required=True, type=Path)
    ap.add_argument("--class-map", required=True, type=Path, help="JSON: every model class NAME -> canonical manifest name")
    ap.add_argument("--split", choices=("validation", "test"), default="validation")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--conf", type=float, default=0.15)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--imgsz", type=int, default=1024)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    try:
        if not 0 <= args.conf <= 1 or not 0 < args.iou <= 1 or args.imgsz <= 0:
            raise ValueError("invalid confidence, IoU or image size")
        doc = load_manifest(args.manifest)
        rows = [r for r in doc["samples"] if r["split"] == args.split]
        if not rows:
            raise ValueError(f"no samples in {args.split}")
        mapping = json.loads(args.class_map.read_text())
        import ultralytics
        from ultralytics import YOLO
        model = YOLO(str(args.weights))
        names = model.names
        names = dict(enumerate(names)) if isinstance(names, list) else names
        if set(mapping) != set(names.values()) or any(v not in doc["classes"].values() for v in mapping.values()):
            raise ValueError("map must explicitly cover every model class name with a canonical class")
        frames, elapsed = [], []
        for row in rows:
            image = local_path(args.manifest.parent, row["image"])
            truth = read_labels(local_path(args.manifest.parent, row["labels"]), doc["classes"])
            started = time.perf_counter()
            result = model.predict(source=str(image), conf=args.conf, imgsz=args.imgsz,
                                   device=args.device, verbose=False)[0]
            predictions = []
            if result.boxes is not None:
                # Tensor transfers are inside the measured latency, including GPU sync.
                coords = result.boxes.xyxyn.cpu().tolist()
                labels = result.boxes.cls.cpu().tolist()
                confidence = result.boxes.conf.cpu().tolist()
                for xyxy, label, conf in zip(coords, labels, confidence):
                    predictions.append(Box(mapping[names[int(label)]], tuple(xyxy), conf))
            elapsed.append(1000 * (time.perf_counter() - started))
            frames.append((truth, predictions))
        report = evaluate_frames(frames, doc["classes"].values(), threshold=args.iou)
        report.update(schema_version=1, split=args.split, manifest_sha256=sha256(args.manifest),
                      weights_sha256=sha256(args.weights), class_map=mapping, conf=args.conf,
                      imgsz=args.imgsz, device=args.device, ultralytics_version=ultralytics.__version__,
                      recordings=len({r["recording_id"] for r in rows}),
                      latency_ms={"scope": "predict plus output transfer; first call includes warmup",
                                  "samples": len(elapsed), "first": elapsed[0],
                                  "median": statistics.median(elapsed), "max": max(elapsed)},
                      limitations=["fixed-threshold detection metrics, not mAP",
                                   "negative detections are not measured false taps",
                                   "no outcome accuracy or live-device performance measured"])
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(json.dumps(report, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        ap.exit(2, f"evaluation refused: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
