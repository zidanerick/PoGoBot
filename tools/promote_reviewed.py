#!/usr/bin/env python3
"""Promote human-reviewed frames from the review queue into a training split.

The bot never writes training data directly. `pogobot/learning.py` curates frames into
`datasets/active_v2/review/` with the detector's own predictions as a starting point,
marked `verified: false` in `ledger.jsonl`. Training on those unverified labels is
self-training, and self-training is what degraded the v1 detector (3.23 -> 2.38
detections per frame over three self-retrain generations).

Workflow:
  1. Run the bot. Frames land in datasets/active_v2/review/{images,labels}/.
  2. Fix the labels by hand (Roboflow, labelImg, whatever) - this is the step that
     actually adds information the model does not already have.
  3. Record exhaustive approval and hashes in a schema-v1 manifest, then copy approved train pairs.
     See docs/PERCEPTION_EVALUATION.md. --yes never substitutes for sample review.

  python3 tools/promote_reviewed.py --list          # what is waiting, worst first
  python3 tools/promote_reviewed.py --promote       # copy approved train pairs into train/
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pogobot.evaluation import load_manifest, local_path

BASE = Path(__file__).resolve().parent.parent
REVIEW = BASE / "datasets" / "active_v2"
PRIORITY = {"refuted": 0, "ambiguous": 1, "routine": 2}


def manifest() -> dict:
    out = {}
    j = REVIEW / "ledger.jsonl"
    if j.exists():
        for line in j.read_text().splitlines():
            try:
                rec = json.loads(line)
                out[rec["stem"]] = rec
            except Exception:
                continue
    return out


def pairs():
    imgs = REVIEW / "review" / "images"
    lbls = REVIEW / "review" / "labels"
    if not imgs.exists():
        return []
    return [(p, lbls / f"{p.stem}.txt") for p in sorted(imgs.glob("*.png"))
            if (lbls / f"{p.stem}.txt").exists()]


def cmd_list() -> int:
    m = manifest()
    rows = []
    for img, lbl in pairs():
        rec = m.get(img.stem, {})
        rows.append((PRIORITY.get(rec.get("review_priority", "routine"), 3), img.stem,
                     rec.get("review_priority", "?"), rec.get("outcome", "?"),
                     len([x for x in lbl.read_text().splitlines() if x.strip()]),
                     rec.get("ambiguous", [])))
    rows.sort()
    if not rows:
        print("review queue is empty")
        return 0
    print(f"{len(rows)} frames awaiting review (label these first):\n")
    print(f"{'priority':11s} {'outcome':10s} {'boxes':>5s}  {'ambiguous confs':22s} stem")
    for _, stem, prio, outcome, boxes, amb in rows:
        print(f"{prio:11s} {outcome:10s} {boxes:5d}  {str(amb)[:22]:22s} {stem}")
    print("\nrefuted  = the bot tapped this and was wrong; the label set is probably missing "
          "or mislabelling the thing it actually hit")
    print("ambiguous = the detector was unsure about an object here; the most informative "
          "frames to correct")
    return 0


def cmd_promote(dest: Path, yes: bool, review_manifest: Path | None = None) -> int:
    """Copy only reviewed train samples, retaining source evidence and provenance."""
    source = review_manifest or REVIEW / "review_manifest.json"
    try:
        doc = load_manifest(source)
        rows = [row for row in doc["samples"] if row["split"] == "train"]
        if not rows:
            raise ValueError("no reviewed train samples; held-out samples cannot be promoted")
        provenance = dest / "review_manifest.json"
        existing = load_manifest(provenance) if provenance.exists() else None
        if existing and existing["classes"] != doc["classes"]:
            raise ValueError("destination class mapping differs")
        if existing is not None and "recording_splits" not in existing:
            raise ValueError("destination provenance lacks recording_splits; add the complete historical assignment registry before promoting")
        if not existing and dest.exists() and any(dest.iterdir()):
            raise ValueError("nonempty destination has no reviewed provenance manifest; migrate explicitly")
        previous = existing["samples"] if existing else []
        ids = {r["id"] for r in previous}
        hashes = {r["review"]["image_sha256"] for r in previous}
        # Keep assignments for every recording, including validation/test recordings
        # omitted by a later source manifest. Never infer a new split for a known ID.
        assignments = dict(existing.get("recording_splits", {})) if existing else {}
        for recording, split in doc.get("recording_splits", {}).items():
            if recording in assignments and assignments[recording] != split:
                raise ValueError(f"immutable recording split changed: {recording} was {assignments[recording]}, now {split}")
            assignments[recording] = split
        for row in doc["samples"]:
            recording, split = row["recording_id"], row["split"]
            if recording in assignments and assignments[recording] != split:
                raise ValueError(f"immutable recording split changed: {recording} was {assignments[recording]}, now {split}")
            assignments[recording] = split
        jobs, exported = [], []
        targets = set()
        for row in rows:
            if row["id"] in ids or row["review"]["image_sha256"] in hashes:
                raise ValueError("sample already promoted")
            if Path(row["image"]).stem != Path(row["labels"]).stem:
                raise ValueError(f"{row['id']}: image and label stems must match for YOLO pairing")
            out = dict(row)
            for field, folder in (("image", "images"), ("labels", "labels")):
                src = local_path(source.parent, row[field])
                relative = str(Path(folder) / src.name)
                target = dest / relative
                if target.exists() or target in targets:
                    raise ValueError(f"destination collision: {target}")
                targets.add(target)
                jobs.append((src, target))
                out[field] = relative
            exported.append(out)
        print(f"{len(rows)} approved training pairs -> {dest}")
        if not yes:
            print("Validated preview. Use --yes to copy; source evidence is retained.")
            return 0
        dest.mkdir(parents=True, exist_ok=True)
        created = []
        try:
            for src, target in jobs:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    created.append(target)
                    with src.open("rb") as input_file:
                        shutil.copyfileobj(input_file, output)
            updated = {"schema_version": 1, "classes": doc["classes"],
                       "recording_splits": dict(sorted(assignments.items())),
                       "samples": previous + exported}
            pending = dest / "review_manifest.pending.json"
            if pending.exists():
                raise ValueError("pending manifest exists; inspect previous interrupted promotion")
            with pending.open("x") as output:
                created.append(pending)
                json.dump(updated, output, indent=2)
                output.write("\n")
            load_manifest(pending)  # verify copied files before publishing provenance
            pending.replace(provenance)
        except Exception:
            for file in reversed(created):
                file.unlink(missing_ok=True)
            raise
        print(f"copied {len(rows)} pairs with reviewed provenance")
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"promotion refused: {exc}")
        return 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show the queue, worst first")
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--dest", type=Path, default=BASE / "datasets" / "det_v3" / "train")
    ap.add_argument("--yes", action="store_true", help="copy approved training files")
    ap.add_argument("--manifest", type=Path, help="reviewed dataset registry (schema v1)")
    a = ap.parse_args()
    if a.promote:
        return cmd_promote(a.dest, a.yes, a.manifest)
    return cmd_list()


if __name__ == "__main__":
    raise SystemExit(main())
