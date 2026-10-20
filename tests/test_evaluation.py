import json
from pathlib import Path

import pytest

from pogobot.evaluation import (Box, bootstrap_outcome_intervals, evaluate_frames,
                                evaluate_outcomes, load_manifest)
from tools.promote_reviewed import cmd_promote


def test_matching_is_class_aware_and_one_to_one_and_counts_false_positives():
    gt = [Box("pokemon", (0.1, 0.1, 0.3, 0.3))]
    pred = [Box("rocket", (0.1, 0.1, 0.3, 0.3), 0.99),
            Box("pokemon", (0.1, 0.1, 0.3, 0.3), 0.9),
            Box("pokemon", (0.1, 0.1, 0.3, 0.3), 0.8)]
    result = evaluate_frames([(gt, pred), ([], [Box("pokemon", (0.5, .5, .6, .6))]),
                              ([], [])], ["pokemon", "rocket"])
    assert result["per_class"]["pokemon"]["tp"] == 1
    assert result["per_class"]["pokemon"]["fp"] == 2
    assert result["per_class"]["rocket"]["fp"] == 1
    assert result["per_class"]["pokemon"]["fn"] == 0
    assert result["negative_frames"] == 2
    assert result["negative_false_positive_frames"] == 1
    assert result["negative_frame_false_positive_rate"] == .5


def test_iou_boundary_and_unmatched_truth_are_counted():
    result = evaluate_frames([( [Box("a", (0, 0, .5, .5))],
                                [Box("a", (.5, .5, .8, .8))])], ["a"], threshold=.5)
    assert result["micro"]["tp"] == 0
    assert result["micro"]["fp"] == result["micro"]["fn"] == 1


def test_outcomes_count_one_per_action_and_unknown_as_abstention():
    rows = [
        {"action_id": "1", "recording_id": "a", "truth": "caught", "prediction": "caught", "reviewed": True, "evidence": "a/1.png"},
        {"action_id": "2", "recording_id": "a", "truth": "fled", "prediction": "unknown", "reviewed": True, "evidence": "a/2.png"},
        {"action_id": "3", "recording_id": "b", "truth": "broke_out", "prediction": "caught", "reviewed": True, "evidence": "b/3.png"},
    ]
    result = evaluate_outcomes(rows)
    assert result["episodes"] == 3 and result["recordings"] == 2
    assert result["coverage"] == pytest.approx(2 / 3)
    assert result["covered_accuracy"] == .5
    assert result["correct_all_episodes"] == pytest.approx(1 / 3)
    assert result["abstentions"] == 1
    assert result["confusion"]["fled"]["unknown"] == 1


def test_outcome_intervals_resample_recordings_deterministically_and_mark_small_samples():
    rows = [
        {"action_id": "a1", "recording_id": "a", "truth": "caught", "prediction": "caught", "reviewed": True, "evidence": "a/1.png"},
        {"action_id": "a2", "recording_id": "a", "truth": "caught", "prediction": "caught", "reviewed": True, "evidence": "a/2.png"},
        {"action_id": "b1", "recording_id": "b", "truth": "caught", "prediction": "fled", "reviewed": True, "evidence": "b/1.png"},
        {"action_id": "c1", "recording_id": "c", "truth": "fled", "prediction": "fled", "reviewed": True, "evidence": "c/1.png"},
    ]
    first = bootstrap_outcome_intervals(rows, resamples=250, confidence=.90, seed=7)
    second = bootstrap_outcome_intervals(rows, resamples=250, confidence=.90, seed=7)
    assert first == second
    assert first["method"] == "recording_cluster_percentile_bootstrap"
    assert first["independent_recordings"] == 3
    interval = first["intervals"]["correct_all_episodes"]
    assert 0 <= interval["lower"] <= interval["upper"] <= 1
    single = bootstrap_outcome_intervals(rows[:2], resamples=20)
    assert single["intervals"] is None
    assert "two independent recordings" in single["note"]


def test_outcome_metrics_refuse_unreviewed_or_duplicate_actions():
    base = {"action_id": "1", "recording_id": "a", "truth": "caught", "prediction": "caught", "reviewed": True, "evidence": "frame.png"}
    with pytest.raises(ValueError, match="unique"):
        evaluate_outcomes([base, dict(base)])
    with pytest.raises(ValueError, match="reviewed"):
        evaluate_outcomes([dict(base, reviewed=False)])


def make_manifest(root: Path, *, split="train", approved=True):
    (root / "images").mkdir(parents=True)
    (root / "labels").mkdir()
    image, labels = root / "images" / "a.png", root / "labels" / "a.txt"
    image.write_bytes(b"image bytes")
    labels.write_text("0 0.5 0.5 0.2 0.2\n")
    import hashlib
    h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    doc = {"schema_version": 1, "classes": {"0": "pokemon"}, "samples": [{
        "id": "a", "recording_id": "session-a", "split": split,
        "image": "images/a.png", "labels": "labels/a.txt",
        "review": {"approved": approved, "exhaustive": True, "reviewer": "r", "image_sha256": h(image), "labels_sha256": h(labels)}
    }]}
    path = root / "manifest.json"
    path.write_text(json.dumps(doc))
    return path, doc, image, labels


def test_manifest_rejects_modified_file_and_split_leakage(tmp_path):
    path, doc, image, _ = make_manifest(tmp_path)
    load_manifest(path)
    image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed since review"):
        load_manifest(path)
    image.write_bytes(b"image bytes")
    doc["samples"].append(dict(doc["samples"][0], id="b", split="test"))
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="split leakage"):
        load_manifest(path)


def test_manifest_rejects_unreviewed_labels_and_path_escape(tmp_path):
    path, doc, _, _ = make_manifest(tmp_path, approved=False)
    with pytest.raises(ValueError, match="approval"):
        load_manifest(path)
    doc["samples"][0]["review"]["approved"] = True
    doc["samples"][0]["image"] = "../outside.png"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="escapes"):
        load_manifest(path)


def test_promotion_copies_only_approved_training_data_and_records_provenance(tmp_path):
    path, _, image, labels = make_manifest(tmp_path / "source")
    dest = tmp_path / "dest"
    assert cmd_promote(dest, False, path) == 0
    assert not dest.exists()
    assert cmd_promote(dest, True, path) == 0
    assert (dest / "images/a.png").read_bytes() == image.read_bytes()
    assert (dest / "labels/a.txt").read_bytes() == labels.read_bytes()
    assert image.exists() and labels.exists()
    manifest = load_manifest(dest / "review_manifest.json")
    assert manifest["samples"][0]["split"] == "train"
    assert cmd_promote(dest, True, path) == 2


def test_promotion_refuses_held_out_sample(tmp_path):
    path, _, _, _ = make_manifest(tmp_path / "source", split="test")
    assert cmd_promote(tmp_path / "dest", True, path) == 2


def make_outcome_manifest(root: Path, *, split="validation"):
    from tools.evaluate_outcomes import load_episode_manifest
    root.mkdir(parents=True)
    frame = root / "result.png"
    frame.write_bytes(b"result frame")
    import hashlib
    doc = {"schema_version": 1, "predictor": {"name": "screen-state", "version": "candidate-1", "artifact_sha256": "a" * 64},
           "episodes": [{"action_id": "enc-1", "recording_id": "session-1", "split": split,
                         "truth": "caught", "prediction": "caught", "reviewed": True,
                         "reviewer": "r", "evidence": [{"path": "result.png",
                         "sha256": hashlib.sha256(frame.read_bytes()).hexdigest()}]}]}
    path = root / "outcomes.json"
    path.write_text(json.dumps(doc))
    return path, doc, frame, load_episode_manifest


def test_outcome_manifest_binds_predictions_to_reviewed_evidence_and_recording_split(tmp_path):
    path, doc, frame, load_manifest = make_outcome_manifest(tmp_path / "episodes")
    assert load_manifest(path)["episodes"]
    frame.write_bytes(b"different frame")
    with pytest.raises(ValueError, match="changed since review"):
        load_manifest(path)
    frame.write_bytes(b"result frame")
    doc["episodes"].append(dict(doc["episodes"][0], action_id="enc-2", split="test"))
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="split leakage"):
        load_manifest(path)


def test_outcome_manifest_requires_predictor_and_review(tmp_path):
    path, doc, _, load_manifest = make_outcome_manifest(tmp_path / "episodes")
    doc["episodes"][0]["reviewed"] = False
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="human outcome review"):
        load_manifest(path)


def test_outcome_cli_writes_machine_readable_metrics(tmp_path):
    import subprocess
    import sys

    path, _, _, _ = make_outcome_manifest(tmp_path / "episodes")
    output = tmp_path / "metrics.json"
    result = subprocess.run([sys.executable, "tools/evaluate_outcomes.py", "--manifest", str(path),
                            "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert report["episodes"] == 1
    assert report["covered_accuracy"] == 1.0
    assert report["predictor"]["version"] == "candidate-1"
    assert report["uncertainty"]["intervals"] is None


def test_evidence_sampling_is_bounded_and_rejects_invalid_csv(tmp_path):
    from tools.extract_outcome_evidence import parse_events, sample_times

    actions = tmp_path / "actions.csv"
    actions.write_text("action_id,timestamp_s\nthrow-1,5.0\n")
    events = parse_events(actions)
    samples = sample_times(events, before=2, after=3, interval=1, max_frames=8)
    assert samples["throw-1"] == [3, 4, 5, 6, 7, 8]
    with pytest.raises(ValueError, match="above --max-frames"):
        sample_times(events, before=2, after=12, interval=.5, max_frames=10)
    actions.write_text("wrong,header\n")
    with pytest.raises(ValueError, match="action_id,timestamp_s"):
        parse_events(actions)


def test_video_extractor_writes_hashed_unreviewed_frames_without_cv2_install(tmp_path, monkeypatch):
    import hashlib
    import sys
    import types
    from tools.extract_outcome_evidence import extract

    class FakeCapture:
        def __init__(self, path): self.ts = 0; self.released = False
        def isOpened(self): return True
        def set(self, prop, value): self.ts = value; return True
        def read(self): return True, self.ts
        def release(self): self.released = True

    def fake_imwrite(path, frame, options):
        Path(path).write_bytes(str(frame).encode())
        return True

    fake = types.SimpleNamespace(VideoCapture=FakeCapture, CAP_PROP_POS_MSEC=0,
                                 IMWRITE_JPEG_QUALITY=1, imwrite=fake_imwrite)
    monkeypatch.setitem(sys.modules, "cv2", fake)
    out = tmp_path / "session.json"
    doc = extract(tmp_path / "fake.mp4", [{"action_id": "a/1", "timestamp_s": 3.0}], out,
                  recording_id="session-a", split="train", before=1, after=1,
                  interval=1, max_frames=5)
    episode = doc["episodes"][0]
    assert episode["reviewed"] is False and episode["truth"] == "unknown"
    assert len(episode["evidence"]) == 3
    for item in episode["evidence"]:
        frame = tmp_path / item["path"]
        assert hashlib.sha256(frame.read_bytes()).hexdigest() == item["sha256"]
