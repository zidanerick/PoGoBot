#!/usr/bin/env python3
"""Extract bounded screenshot evidence around encounter actions for human annotation.

Input CSV columns: action_id,timestamp_s. No game outcome is inferred by this tool.
The output manifest deliberately contains unknown/unreviewed labels and cannot be scored
until a reviewer fills them and sets reviewed=true.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


def parse_events(path: Path) -> list[dict]:
    events = []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        if not {"action_id", "timestamp_s"}.issubset(reader.fieldnames or ()):
            raise ValueError("CSV must have action_id,timestamp_s columns")
        ids = set()
        for row in reader:
            action_id = (row.get("action_id") or "").strip()
            if not action_id or action_id in ids:
                raise ValueError("action_id values must be nonempty and unique")
            ids.add(action_id)
            try:
                timestamp = float(row["timestamp_s"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{action_id}: timestamp_s must be numeric") from exc
            if not 0 <= timestamp < float("inf"):
                raise ValueError(f"{action_id}: timestamp_s must be finite and nonnegative")
            events.append({"action_id": action_id, "timestamp_s": timestamp})
    if not events:
        raise ValueError("CSV contains no action rows")
    return events


def sample_times(events: list[dict], before: float, after: float, interval: float,
                 max_frames: int) -> dict[str, list[float]]:
    if before < 0 or after < 0 or interval <= 0 or max_frames < 1:
        raise ValueError("before/after must be nonnegative; interval/max-frames must be positive")
    out = {}
    for event in events:
        start = max(0.0, event["timestamp_s"] - before)
        end = event["timestamp_s"] + after
        count = int((end - start) / interval) + 1
        if count > max_frames:
            raise ValueError(f"{event['action_id']}: window needs {count} frames, above --max-frames={max_frames}")
        out[event["action_id"]] = [start + i * interval for i in range(count)]
    return out


def _safe_stem(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")[:64]
    return slug or "action"


def extract(video: Path, events: list[dict], output: Path, *, recording_id: str,
            split: str, before: float, after: float, interval: float,
            max_frames: int) -> dict:
    if split not in {"train", "validation", "test"}:
        raise ValueError("split must be train, validation or test")
    timeline = sample_times(events, before, after, interval, max_frames)
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("install the project's opencv-python requirement to read video") from exc
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {video}")
    frames_dir = output.parent / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    episodes, created = [], []
    try:
        for event in events:
            evidence = []
            event_tag = hashlib.sha256(event["action_id"].encode()).hexdigest()[:8]
            for frame_index, ts in enumerate(timeline[event["action_id"]]):
                cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                filename = (f"{_safe_stem(event['action_id'])}_{event_tag}_"
                            f"{frame_index:03d}_{int(round(ts * 1000)):010d}.jpg")
                path = frames_dir / filename
                if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                    raise OSError(f"could not write evidence frame: {path}")
                created.append(path)
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                evidence.append({"path": str(Path("frames") / filename), "sha256": digest})
            if not evidence:
                raise ValueError(f"{event['action_id']}: no decodable frames in requested window")
            episodes.append({
                "action_id": event["action_id"],
                "recording_id": recording_id,
                "split": split,
                "timestamp_s": event["timestamp_s"],
                "truth": "unknown",
                "prediction": "unknown",
                "reviewed": False,
                "reviewer": "",
                "evidence": evidence,
            })
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    finally:
        cap.release()
    manifest = {"schema_version": 1,
                "predictor": {"name": "pending", "version": "pending", "artifact_sha256": "0" * 64},
                "episodes": episodes}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--actions", type=Path, required=True, help="CSV with action_id,timestamp_s")
    ap.add_argument("--recording-id", required=True)
    ap.add_argument("--split", choices=("train", "validation", "test"), required=True)
    ap.add_argument("--output", type=Path, required=True, help="manifest JSON output path")
    ap.add_argument("--before", type=float, default=2.0, help="seconds before each action")
    ap.add_argument("--after", type=float, default=12.0, help="seconds after each action")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between extracted frames")
    ap.add_argument("--max-frames", type=int, default=64, help="per-action safety limit")
    args = ap.parse_args()
    try:
        events = parse_events(args.actions)
        manifest = extract(args.video, events, args.output, recording_id=args.recording_id,
                           split=args.split, before=args.before, after=args.after,
                           interval=args.interval, max_frames=args.max_frames)
        print(f"wrote {args.output} with {len(manifest['episodes'])} unreviewed episodes")
        print("Review every sequence, fill truth/prediction/reviewer and predictor identity, "
              "then set reviewed=true before scoring.")
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        ap.exit(2, f"evidence extraction refused: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
