# Perception evaluation and evidence

## What is measurable today

The checked-out project has frame-level detector labels and hand-collected screen fixtures, but no committed encounter-result corpus. The detector outputs target boxes, not outcomes such as catch, flee or breakout. Therefore this change supplies an honest detector evaluator and an episode-level scoring contract; it does not claim catch/outcome accuracy. The active-review ledger under `datasets/active_v2` records the model's suggestions with `verified: false`, and is not ground truth.

`tools/evaluate_detector.py` scores a single checkpoint against a human-reviewed manifest. It reports per-class TP/FP/FN, precision, recall, F1, micro counts, false-positive frames among explicitly empty negative frames, recording count, and prediction latency. Matching is confidence-ordered and one-to-one at the requested IoU threshold. The class map must explicitly map every checkpoint class name to a canonical dataset class. Run both validation and test only after model/threshold choices are fixed; keep test results sealed during tuning. These fixed-threshold metrics are not mAP, false taps, catch rates, or proof of live behavior.

The former `adopt_best_detector.py --install` compared class-agnostic recall and copied a winner directly into deployed weights. That selector is retired. Evaluation now emits evidence and leaves checkpoint selection for reviewed promotion.

## Manifest schema version 1

Store a JSON file beside the files it references. `classes` maps consecutive YOLO IDs to unique canonical names. Every sample references one image and one YOLO label file. An empty label file means a human reviewer checked the full frame and confirmed no objects from the declared classes. Each recording has exactly one split, which prevents adjacent frames from the same capture leaking between train, validation and test. Each image and label must be approved as exhaustive and bound to their current SHA-256 hashes.

```json
{
  "schema_version": 1,
  "classes": {"0": "pokemon", "1": "pokestop", "2": "rocket"},
  "samples": [{
    "id": "walk-2026-10-18-001-frame-00042",
    "recording_id": "walk-2026-10-18-001",
    "split": "validation",
    "image": "images/frame-00042.png",
    "labels": "labels/frame-00042.txt",
    "review": {
      "approved": true,
      "exhaustive": true,
      "reviewer": "human-initials",
      "image_sha256": "<64 lowercase hex characters>",
      "labels_sha256": "<64 lowercase hex characters>"
    }
  }]
}
```

Replace the placeholders with hashes computed after final image and label edits. `exhaustive` is a reviewer assertion, not an automated guarantee. Validate annotations from different screen states, device sizes and sessions. Include difficult negatives and all objects that could cause an action. Record provenance and consent for any captured material; do not include account identifiers or credentials in filenames/metadata.

Run:

```sh
python tools/evaluate_detector.py \
  --manifest datasets/reviewed/manifest.json \
  --weights models/v3/det/weights/best.pt \
  --class-map datasets/reviewed/model-class-map.json \
  --split validation --device cpu --output reports/detector-validation.json
```

`model-class-map.json` is a JSON object whose keys exactly match all names in `model.names`, and whose values match canonical manifest class names, for example `{"pokemon_go":"pokemon","pokestop":"pokestop","rocket_battle":"rocket"}`. Select the confidence threshold on validation data. Then run the untouched test split with the frozen threshold. Report sample and recording counts beside every metric. Keep test recordings disjoint from training and validation recordings.

Promotion uses the same approved manifest and copies only `train` samples. It refuses changed files, unreviewed labels, duplicate samples, class-map changes, and nonempty destinations without provenance. It retains the source and writes a checked manifest next to copied training data:

```sh
python tools/promote_reviewed.py --promote --manifest datasets/reviewed/manifest.json
python tools/promote_reviewed.py --promote --manifest datasets/reviewed/manifest.json --yes
```

## Outcome labels and metrics

Outcome scoring uses one row per completed interaction/action ID, not one row per video frame. Annotate `caught`, `fled`, `broke_out`, `still_in_encounter`, `out_of_balls`, or `unknown`. Truth requires a reviewer and an evidence reference to the relevant frame sequence. Predictions come from the bot trace at the time it would act. `unknown` is an explicit abstention; it is never silently scored as success. `tools/evaluate_outcomes.py` validates and scores a hash-bound episode manifest. The pure `evaluate_outcomes` function returns episode count, recording count, coverage, accuracy among covered predictions, accuracy over all episodes, abstentions and a confusion matrix. The manifest requires a predictor name/version and SHA-256 artifact hash, reviewer, evidence image paths and hashes, action IDs, and one split per recording. Hash the checkpoint or the versioned rule package used to produce predictions. Split by recording before analysis. Predictions can be exported from a candidate result classifier; this tool scores them but does not train or run a result classifier.

```json
{
  "schema_version": 1,
  "predictor": {"name": "screen-state", "version": "candidate-1", "artifact_sha256": "<64 lowercase hex characters>"},
  "episodes": [{
    "action_id": "encounter-0001",
    "recording_id": "walk-2026-10-18-001",
    "split": "validation",
    "truth": "caught",
    "prediction": "unknown",
    "reviewed": true,
    "reviewer": "human-initials",
    "evidence": [{"path": "frames/000142.png", "sha256": "<64 lowercase hex characters>"}]
  }]
}
```

Run it with `python tools/evaluate_outcomes.py --manifest datasets/outcomes/manifest.json --split validation --output reports/outcome-validation.json`. Use `--split test` only after the predictor and decision threshold are frozen. An episode with multiple evidence frames still contributes one outcome. Each listed evidence path must stay inside the manifest directory and match its reviewed SHA-256.

Use separate measurements for screen classification, target detection, game outcome and action quality. For a supervised run, link each outcome episode to action ID and report: readiness/launch precision, breakout/catch/flee confusion, unknown rate, false taps on negative screens, successful launches per requested throw, balls per confirmed catch, observed stop rewards, and active-device latency. A UI appearing is not proof an item was awarded. If the available pixels cannot distinguish a catch from a flee, keep the label unknown and gather more evidence.

## Preparing encounter recordings

`tools/extract_outcome_evidence.py` samples bounded evidence windows from a local screen recording. Supply a CSV with one row per throw/action and its timestamp in seconds from the start of the recording. Use a recording ID and split consistently for all events from that recording:

```csv
action_id,timestamp_s
enc-0001-throw-01,42.5
enc-0001-throw-02,61.0
```

```sh
python tools/extract_outcome_evidence.py \
  --video recordings/session-01.mp4 \
  --actions recordings/session-01-actions.csv \
  --recording-id session-01 --split train \
  --output datasets/outcomes/session-01.json
```

The defaults save frames from two seconds before through twelve seconds after each action at one-second intervals, with a 64-frame per-action limit. Adjust the window to match the event; keep adjacent events from the same recording in one split. The tool does not infer outcomes. It writes `unknown` labels with `reviewed: false`, so its output must be reviewed and completed before scoring. Keep recordings and generated screenshots local unless you have checked them for account names, location details and other private screen content.

## Current data limitation

At this checkout, `datasets/det_v3/valid` referenced by the previous evaluator and README is not tracked. The golden screen tests also skip when their labelled corpus is absent. No outcome examples are available to calculate real accuracy yet. The first data task is a small, consented capture set with complete outcome evidence and recording-level splits, including negatives and ambiguous transitions. Until then, the evaluator's output remains unavailable rather than fabricated.
