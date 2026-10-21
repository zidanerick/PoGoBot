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

Promotion uses the same approved manifest and copies only `train` samples. It refuses changed files, unreviewed labels, duplicate samples, class-map changes, incompatible image/label filename stems, and nonempty destinations without provenance. It retains the source and writes a checked manifest next to copied training data. That promoted manifest also stores a `recording_splits` map for every assignment in the source manifest, including validation and test recordings that were not copied. Later promotions merge new assignments from both source samples and an optional source `recording_splits` registry, and refuse any changed assignment, even when that recording was omitted from the later sample list.

Existing promoted datasets created before this registry was added have no way to reveal omitted validation/test assignments. Promotion therefore refuses a destination provenance manifest without `recording_splits`; add a complete historical recording-to-split registry before continuing. This fail-closed migration is necessary because the missing assignments cannot be reconstructed from the copied training files alone.

```sh
python tools/promote_reviewed.py --promote --manifest datasets/reviewed/manifest.json
python tools/promote_reviewed.py --promote --manifest datasets/reviewed/manifest.json --yes
```

## Outcome labels and metrics

Outcome scoring uses one row per action ID, not one row per video frame. A reviewed truth is `caught`, `fled`, `broke_out`, `still_in_encounter`, `out_of_balls`, or `unknown`. `unknown` truth means a human reviewer could not determine the result from the available evidence. Such rows remain in corpus and truth-resolution counts but are excluded from class accuracy and confusion counts. `unknown` prediction means the system abstained; it remains in the denominator of resolved truth and lowers prediction coverage and all-resolved accuracy. Neither unknown state is silently scored as success. Truth review must be completed without seeing the candidate prediction where practical. Predictions come from the frozen bot trace at the time it would act.

`tools/evaluate_outcomes.py` validates and scores a hash-bound episode manifest. It reports total episodes, recordings, resolved and unresolved truth counts, truth-resolution rate, prediction coverage, accuracy among covered predictions, accuracy over resolved episodes, abstentions, and a confusion matrix. It also reports percentile uncertainty intervals from a recording-cluster bootstrap. It resamples recordings as groups, not individual episodes, because outcomes within one recording are correlated. Intervals are withheld when fewer than two recordings are present; with few recordings, even a computed interval is only a rough uncertainty indicator. The manifest requires a predictor name/version and SHA-256 artifact hash, reviewer, evidence image paths and hashes, action IDs, and a persistent `recording_splits` registry. Hash the checkpoint or the versioned rule package used to produce predictions. Assign splits once, by recording, before annotation or tuning. Preserve the full registry when records are added or omitted, and refuse any assignment change. Predictions can be exported from a candidate result classifier; this tool scores them but does not train or run a result classifier.

```json
{
  "schema_version": 1,
  "recording_splits": {"walk-2026-10-18-001": "validation"},
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

Run it with `python tools/evaluate_outcomes.py --manifest datasets/outcomes/manifest.json --split validation --output reports/outcome-validation.json`. Use `--split test` only after the predictor and decision threshold are frozen. Set `--bootstrap-samples`, `--confidence` and `--seed` to control the deterministic interval calculation. An episode with multiple evidence frames still contributes one outcome. Each listed evidence path must stay inside the manifest directory and match its reviewed SHA-256. A complete registry may contain recordings with no currently included episode rows, so assignments remain durable when manifests are filtered or appended.

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

The defaults save frames from two seconds before through twelve seconds after each action at one-second intervals, with a 64-frame per-action limit. Adjust the window to match the event; keep adjacent events from the same recording in one split. The tool does not infer outcomes. It writes `unknown` labels with `reviewed: false`, so its output must be reviewed and completed before scoring. Extraction is append-only: an existing manifest or any planned frame path causes an immediate refusal. If frame extraction or manifest writing fails partway through, only files created by that attempt are removed; pre-existing files are left untouched. Use a new output directory for a separate run. Keep recordings and generated screenshots local unless you have checked them for account names, location details and other private screen content.

The extractor tests replace OpenCV with a fake capture and fake image writer. They verify timestamp requests, hashes of the bytes returned by the fake writer, collision refusal, and rollback after simulated partial frame/manifest writes. They do **not** exercise MP4 decoding, codec seeking, actual screenshot contents, device capture, or gameplay. Before relying on a corpus, follow the separate real-video checks in [the held-out gameplay corpus protocol](HELD_OUT_GAMEPLAY_CORPUS.md).

## Current data limitation

At this checkout, `datasets/det_v3/valid` referenced by the previous evaluator and README is not tracked. The golden screen tests also skip when their labelled corpus is absent. No outcome examples are available to calculate real accuracy yet. The first data task is a small, consented capture set with complete outcome evidence and recording-level splits, including negatives and ambiguous transitions. Until then, the evaluator's output remains unavailable rather than fabricated.
## T01 evidence adapter seam

`pogobot/evidence_adapter.py` prepares the legacy `Frame`/`Observation` outputs for T01's proposed `FrameRef` and `Evidence` interfaces. It accepts the lead-owned contract constructors by injection and requires the capture boundary to supply the original `FrameRef`; it does not define a competing shared contract or stamp identity during perception. It refuses frame, observation and reference mismatches, emits evidence only for explicit signals and detector positives, and leaves optical scores out of calibrated confidence. Until the shared runtime types land and this adapter is wired to the live perception path, it is an offline compatibility seam, not runtime evidence or an accuracy result. Its tests use T01-shaped local test dataclasses and the existing legacy perception dataclasses.
