# Held-out gameplay corpus protocol

This protocol produces reviewed evidence for evaluating encounter outcomes. It is a collection and review plan, not an accuracy result. No detector or encounter accuracy claim is supported until the frozen evaluator has been run against the reviewed, held-out test recordings and the report has been checked.

## 1. Freeze the protocol before capture

Write down the target use, outcome definitions, capture settings, predictor artifact and version, action/event definition, required evidence window, exclusions, and planned metrics before looking at test results. Include the game/app build, device model, screen resolution and scale, capture method, bot commit, model or rule artifact SHA-256, and any decision threshold in the run record. Do not tune the model, thresholds, or labeling rules against the test set.

Use a local, consented capture of ordinary gameplay on an account the operator is permitted to use. Do not automate play solely to manufacture examples or include account credentials, player identifiers, or precise location data in filenames or manifests. Keep source video access restricted; create a redacted copy if identifiers appear in evidence frames. Retain the source video checksum and a stable, non-identifying recording ID in a private capture ledger.

## 2. Define the unit and assign immutable splits

The split unit is a complete recording/session, never an individual frame, encounter, or action. Give each source recording a stable ID and assign it once to `train`, `validation`, or `test` before annotation. Store every assignment in the canonical `recording_splits` registry. Do not reassign an ID, even if a later manifest omits that recording. Keep all episodes and derived frames from one source recording in that same split. If recordings are fragments of one continuous session, group them under one session assignment before splitting.

Use training recordings to fit the predictor. Use validation recordings to choose model versions, thresholds, and review rules. Seal test recordings and labels from anyone tuning those choices. Open the test split once the predictor artifact and thresholds are frozen. If a test result drives any changes, that test set has become development data; collect and reserve new recordings for the next held-out evaluation.

Aim for multiple independent recordings per split and representation of each outcome and important operating condition. Record class counts and missing classes. A recording-cluster bootstrap estimates between-recording variation; it cannot make a small or biased corpus representative. Do not split a single long capture into multiple nominal recordings to inflate the independent recording count.

## 3. Capture complete action-linked evidence

Record the screen continuously at a stable, known rate and preserve the original file unchanged. Separately log every eligible action with a unique action ID, recording ID, timestamp on the video timeline, action type, whether the input was accepted or its execution was uncertain, and any relevant runtime stop/timeout event. Include failed, interrupted, and ambiguous actions. Do not select only successful or visually clear encounters.

For each action, retain enough frames before and after it to establish the prior state, the action, and the resulting state. Keep source video SHA-256, action-log SHA-256, capture metadata, extractor version/commit, extracted frame paths, frame SHA-256 values, and the evidence-window parameters in the audit record. Extraction output starts as `reviewed: false` and `truth: unknown`; it is only an indexing aid. Verify timestamps and frame contents against the original video before annotation.

## 4. Human review procedure

Review the full evidence sequence, not a single convenient frame. Hide the model prediction and confidence from truth reviewers when practical. Each reviewer records the action ID, label, reviewer ID, evidence references, and a short reason for ambiguous or excluded cases.

Use these truth labels:

- `caught`: evidence confirms the encounter ended in a catch.
- `fled`: evidence confirms the target fled.
- `broke_out`: evidence confirms a breakout and the encounter continues.
- `still_in_encounter`: the evidence window ends with the encounter still active, with no confirmed terminal outcome.
- `out_of_balls`: the game explicitly reports no usable balls remain.
- `unknown`: evidence is missing, contradictory, occluded, or insufficient to distinguish the result.

Do not infer a catch from a disappearing target, animation, or UI transition alone. Do not label an uncertain input as a confirmed throw. Preserve separate action-execution uncertainty and outcome uncertainty. A reviewed `unknown` truth is a valid corpus record, but it is excluded from class accuracy and counted in truth-resolution reporting. A system prediction of `unknown` is an abstention and counts against coverage on resolved truths.

Have a second reviewer independently label every reserved test episode without seeing the first review or model prediction. Adjudicate disagreements with a documented rationale. Keep both original labels and the adjudicated result. If reviewers cannot resolve the evidence, keep `unknown`. For train and validation, double-review a predefined sample and all ambiguous/high-impact categories; report the procedure and disagreement rate.

## 5. Build and validate the manifests

Maintain one canonical episode manifest containing all splits and the full immutable `recording_splits` registry. Merge extracted recording manifests without dropping registry entries. Require unique action IDs, consistent recording IDs and splits, reviewed truth, reviewer IDs, a frozen predictor name/version/hash, and evidence frame paths whose SHA-256 values match. Keep evidence inside the manifest directory. Preserve the source video and action-log hashes in the capture ledger so frame hashes can be traced to the original source.

Before scoring, validate that:

1. Every included recording ID has exactly one registry assignment and all its episodes use that split.
2. No source recording, session fragment, or derived frame appears across splits.
3. Every resolved truth has sufficient human-reviewed evidence; unresolved truths remain `unknown`.
4. Every test prediction was generated by the frozen artifact and threshold without test-guided tuning.
5. Evidence and manifest hashes are recorded in the report, and any corrupt or missing files are refused rather than silently skipped.

## 6. Run the held-out evaluation and report limits

Run validation during development. After freezing the artifact and threshold, run `tools/evaluate_outcomes.py` with `--split test` against the complete canonical manifest. Report episode count, independent recording count, truth-resolution count/rate, unknown prediction count/rate, prediction coverage, covered accuracy, accuracy over resolved episodes, per-class confusion/support, and recording-cluster uncertainty intervals where available. Include the artifact hash, manifest hash, split registry revision, capture conditions, reviewer agreement/adjudication method, and exclusions.

Do not call these numbers live-device accuracy, catch rate, or general game performance. They describe only the reviewed test corpus and its capture conditions. State when a class is absent, truth is frequently unknown, there are too few independent recordings for intervals, or a recording/codec/device condition was not tested. Do not publish a detector or encounter accuracy claim before a reviewed held-out corpus has actually been evaluated.

## 7. Separate fake-decoder tests from real-video extraction

The unit tests monkeypatch `cv2.VideoCapture` and `cv2.imwrite`. They exercise path planning, mocked timestamps, hashes of mocked output bytes, collision refusal, and cleanup after injected write failures. They establish transactional behavior under those fakes only. They do not establish that OpenCV can decode a real recording, seek to the requested timestamp, return the expected frame, handle variable frame rate, or preserve usable image content.

Before accepting extracted evidence, run a separate real-video smoke check on a short, consented recording using the installed `opencv-python` and the actual capture codec. Independently compare sampled frames with the source video at known timestamps, confirm readable images and expected image dimensions, check the first/last-window boundary behavior, and exercise one intentionally invalid or truncated file. Record OpenCV version, codec/container, source checksum, timestamps compared, and operator outcome. Run extraction into a new temporary directory and verify frame hashes and manifest references. This is an extraction validation, not model evaluation; it must not be reported as gameplay accuracy.
