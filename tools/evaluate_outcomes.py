#!/usr/bin/env python3
"""Score reviewed encounter predictions from hash-bound screenshot evidence."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pogobot.evaluation import evaluate_outcomes, local_path, sha256


def load_episode_manifest(path: Path) -> dict:
    doc = json.loads(path.read_text())
    if doc.get("schema_version") != 1:
        raise ValueError("unsupported outcome manifest schema_version")
    predictor = doc.get("predictor")
    if not isinstance(predictor, dict) or not all(
            isinstance(predictor.get(k), str) and predictor[k].strip()
            for k in ("name", "version")):
        raise ValueError("predictor name and version are required")
    episodes = doc.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ValueError("manifest must contain episodes")
    recordings, ids = {}, set()
    for row in episodes:
        if not isinstance(row, dict):
            raise ValueError("episode entries must be objects")
        action_id, recording, split = row.get("action_id"), row.get("recording_id"), row.get("split")
        if not isinstance(action_id, str) or not action_id.strip() or action_id in ids:
            raise ValueError("episode action_id values must be nonempty and unique")
        ids.add(action_id)
        if not isinstance(recording, str) or not recording.strip() or split not in {"train", "validation", "test"}:
            raise ValueError(f"{action_id}: recording_id and explicit split required")
        if recording in recordings and recordings[recording] != split:
            raise ValueError(f"recording split leakage: {recording}")
        recordings[recording] = split
        if row.get("reviewed") is not True or not str(row.get("reviewer", "")).strip():
            raise ValueError(f"{action_id}: human outcome review required")
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"{action_id}: at least one evidence frame required")
        seen = set()
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError(f"{action_id}: evidence entries must be objects")
            frame = local_path(path.parent, item.get("path"))
            if frame in seen:
                raise ValueError(f"{action_id}: duplicate evidence path")
            seen.add(frame)
            if sha256(frame) != item.get("sha256"):
                raise ValueError(f"{action_id}: evidence changed since review")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--split", choices=("validation", "test"), default="validation")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    try:
        doc = load_episode_manifest(args.manifest)
        episodes = [row for row in doc["episodes"] if row["split"] == args.split]
        if not episodes:
            raise ValueError(f"no episodes in {args.split}")
        result = evaluate_outcomes(episodes)
        result.update(schema_version=1, split=args.split, predictor=doc["predictor"],
                      manifest_sha256=sha256(args.manifest),
                      evidence_frames=sum(len(row["evidence"]) for row in episodes),
                      limitations=["one vote per reviewed interaction, not per frame",
                                   "unknown predictions are abstentions",
                                   "metrics are meaningful only if test recordings were held out before tuning"])
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        ap.exit(2, f"evaluation refused: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
