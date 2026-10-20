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
    assert result["accuracy_over_resolved_episodes"] == pytest.approx(1 / 3)
    assert result["abstentions"] == 1
    assert result["confusion"]["fled"]["unknown"] == 1


def test_unknown_reviewed_truth_is_retained_but_excluded_from_accuracy():
    rows = [
        {"action_id": "known", "recording_id": "a", "truth": "caught", "prediction": "unknown", "reviewed": True, "evidence": "a/1.png"},
        {"action_id": "unresolved", "recording_id": "a", "truth": "unknown", "prediction": "caught", "reviewed": True, "evidence": "a/2.png"},
    ]
    result = evaluate_outcomes(rows)
    assert result["episodes"] == 2
    assert result["truth_resolved_episodes"] == 1
    assert result["unresolved_truth_episodes"] == 1
    assert result["truth_resolution_rate"] == .5
    assert result["prediction_coverage"] == 0
    assert result["abstentions"] == 1
    assert result["covered_accuracy"] is None
    assert result["accuracy_over_resolved_episodes"] == 0
    assert sum(result["confusion"]["caught"].values()) == 1


def test_all_unknown_reviewed_truth_does_not_create_accuracy_claim():
    row = {"action_id": "unresolved", "recording_id": "a", "truth": "unknown",
           "prediction": "caught", "reviewed": True, "evidence": "a/1.png"}
    result = evaluate_outcomes([row])
    assert result["truth_resolved_episodes"] == 0
    assert result["prediction_coverage"] is None
    assert result["covered_accuracy"] is None
    assert result["accuracy_over_resolved_episodes"] is None


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
    interval = first["intervals"]["accuracy_over_resolved_episodes"]
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


def add_manifest_sample(root: Path, doc: dict, *, sample_id: str, recording_id: str,
                        split: str, image_stem: str | None = None,
                        label_stem: str | None = None):
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "labels").mkdir(parents=True, exist_ok=True)
    image_stem = image_stem or sample_id
    label_stem = label_stem or sample_id
    image, labels = root / "images" / f"{image_stem}.png", root / "labels" / f"{label_stem}.txt"
    image.write_bytes(f"image bytes {sample_id}".encode())
    labels.write_text("0 0.5 0.5 0.2 0.2\n")
    import hashlib
    h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    if "samples" not in doc:
        doc.update(schema_version=1, classes={"0": "pokemon"}, samples=[])
    doc["samples"].append({
        "id": sample_id, "recording_id": recording_id, "split": split,
        "image": str(Path("images") / image.name), "labels": str(Path("labels") / labels.name),
        "review": {"approved": True, "exhaustive": True, "reviewer": "r",
                   "image_sha256": h(image), "labels_sha256": h(labels)}
    })
    return image, labels


def make_manifest(root: Path, *, split="train", approved=True, image_stem="a", label_stem="a"):
    doc = {"schema_version": 1, "classes": {"0": "pokemon"}, "samples": []}
    image, labels = add_manifest_sample(root, doc, sample_id="a", recording_id="session-a",
                                        split=split, image_stem=image_stem, label_stem=label_stem)
    if not approved:
        doc["samples"][0]["review"]["approved"] = False
    path = root / "manifest.json"
    path.write_text(json.dumps(doc))
    return path, doc, image, labels


def test_manifest_rejects_modified_file_and_split_leakage(tmp_path):
    path, doc, image, _ = make_manifest(tmp_path)
    load_manifest(path)
    image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed since review"):
        load_manifest(path)
    image.write_bytes(b"image bytes a")
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


def test_promotion_preserves_all_recording_assignments_when_later_manifest_omits_them(tmp_path):
    source = tmp_path / "source"
    path, doc, _, _ = make_manifest(source)
    add_manifest_sample(source, doc, sample_id="val", recording_id="validation-session", split="validation")
    add_manifest_sample(source, doc, sample_id="test", recording_id="test-session", split="test")
    path.write_text(json.dumps(doc))
    dest = tmp_path / "dest"
    assert cmd_promote(dest, True, path) == 0

    later = tmp_path / "later"
    later.mkdir()
    later_doc = {"schema_version": 1, "classes": {"0": "pokemon"}, "samples": []}
    add_manifest_sample(later, later_doc, sample_id="new", recording_id="new-train-session", split="train")
    later_manifest = later / "manifest.json"
    later_manifest.write_text(json.dumps(later_doc))
    assert cmd_promote(dest, True, later_manifest) == 0
    promoted = load_manifest(dest / "review_manifest.json")
    assert promoted["recording_splits"] == {
        "session-a": "train", "validation-session": "validation",
        "test-session": "test", "new-train-session": "train"
    }

    conflicting = tmp_path / "conflicting"
    conflicting.mkdir()
    conflict_doc = {"schema_version": 1, "classes": {"0": "pokemon"}, "samples": []}
    add_manifest_sample(conflicting, conflict_doc, sample_id="changed", recording_id="validation-session", split="test")
    conflict_manifest = conflicting / "manifest.json"
    conflict_manifest.write_text(json.dumps(conflict_doc))
    assert cmd_promote(dest, True, conflict_manifest) == 2
    assert load_manifest(dest / "review_manifest.json")["recording_splits"]["validation-session"] == "validation"


def test_promotion_refuses_image_label_stem_mismatch(tmp_path):
    path, _, _, _ = make_manifest(tmp_path / "source", image_stem="image-a", label_stem="label-a")
    dest = tmp_path / "dest"
    assert cmd_promote(dest, True, path) == 2
    assert not dest.exists()


def make_outcome_manifest(root: Path, *, split="validation"):
    from tools.evaluate_outcomes import load_episode_manifest
    root.mkdir(parents=True)
    frame = root / "result.png"
    frame.write_bytes(b"result frame")
    import hashlib
    doc = {"schema_version": 1, "recording_splits": {"session-1": split},
           "predictor": {"name": "screen-state", "version": "candidate-1", "artifact_sha256": "a" * 64},
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


def test_outcome_manifest_requires_immutable_recording_split_registry(tmp_path):
    path, doc, _, load_manifest = make_outcome_manifest(tmp_path / "episodes")
    doc["recording_splits"]["session-1"] = "test"
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="recording_splits disagrees"):
        load_manifest(path)
    doc.pop("recording_splits")
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="recording_splits must persist"):
        load_manifest(path)


def test_outcome_manifest_refuses_duplicate_or_escaping_evidence_paths(tmp_path):
    path, doc, _, load_manifest = make_outcome_manifest(tmp_path / "episodes")
    evidence = doc["episodes"][0]["evidence"]
    evidence.append(dict(evidence[0]))
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="duplicate evidence path"):
        load_manifest(path)
    doc["episodes"][0]["evidence"] = [{"path": "../outside.png", "sha256": "0" * 64}]
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="escapes"):
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
    assert doc["recording_splits"] == {"session-a": "train"}
    assert len(episode["evidence"]) == 3
    for item in episode["evidence"]:
        frame = tmp_path / item["path"]
        assert hashlib.sha256(frame.read_bytes()).hexdigest() == item["sha256"]

    manifest_bytes = out.read_bytes()
    frame_bytes = {p: p.read_bytes() for p in (tmp_path / "frames").glob("*.jpg")}
    with pytest.raises(FileExistsError, match="manifest already exists"):
        extract(tmp_path / "fake.mp4", [{"action_id": "a/1", "timestamp_s": 3.0}], out,
                recording_id="session-a", split="train", before=1, after=1,
                interval=1, max_frames=5)
    assert out.read_bytes() == manifest_bytes
    assert {p: p.read_bytes() for p in frame_bytes} == frame_bytes

    second_manifest = tmp_path / "rerun.json"
    with pytest.raises(FileExistsError, match="evidence frame already exists"):
        extract(tmp_path / "fake.mp4", [{"action_id": "a/1", "timestamp_s": 3.0}], second_manifest,
                recording_id="session-a", split="train", before=1, after=1,
                interval=1, max_frames=5)
    assert not second_manifest.exists()
    assert out.read_bytes() == manifest_bytes
    assert {p: p.read_bytes() for p in frame_bytes} == frame_bytes


def test_video_extractor_rolls_back_partial_frames_without_touching_existing_files(tmp_path, monkeypatch):
    import sys
    import types
    from tools.extract_outcome_evidence import extract

    frames = tmp_path / "frames"
    frames.mkdir()
    keep = frames / "keep.jpg"
    keep.write_bytes(b"pre-existing")

    class FakeCapture:
        def __init__(self, path): self.ts = 0
        def isOpened(self): return True
        def set(self, prop, value): self.ts = value; return True
        def read(self): return True, self.ts
        def release(self): pass

    writes = 0
    def fail_second_write(path, frame, options):
        nonlocal writes
        writes += 1
        Path(path).write_bytes(str(frame).encode())
        return writes < 2

    fake = types.SimpleNamespace(VideoCapture=FakeCapture, CAP_PROP_POS_MSEC=0,
                                 IMWRITE_JPEG_QUALITY=1, imwrite=fail_second_write)
    monkeypatch.setitem(sys.modules, "cv2", fake)
    out = tmp_path / "session.json"
    with pytest.raises(OSError, match="temporary evidence frame"):
        extract(tmp_path / "fake.mp4", [{"action_id": "event-1", "timestamp_s": 3.0}], out,
                recording_id="session-a", split="train", before=1, after=1,
                interval=1, max_frames=5)
    assert not out.exists()
    assert list(frames.iterdir()) == [keep]
    assert keep.read_bytes() == b"pre-existing"


def test_video_extractor_rolls_back_frames_when_manifest_write_is_partial(tmp_path, monkeypatch):
    import sys
    import types
    from tools import extract_outcome_evidence as extractor

    frames = tmp_path / "frames"
    frames.mkdir()
    keep = frames / "keep.jpg"
    keep.write_bytes(b"pre-existing")

    class FakeCapture:
        def __init__(self, path): self.ts = 0
        def isOpened(self): return True
        def set(self, prop, value): self.ts = value; return True
        def read(self): return True, self.ts
        def release(self): pass

    def fake_imwrite(path, frame, options):
        Path(path).write_bytes(str(frame).encode())
        return True

    fake = types.SimpleNamespace(VideoCapture=FakeCapture, CAP_PROP_POS_MSEC=0,
                                 IMWRITE_JPEG_QUALITY=1, imwrite=fake_imwrite)
    monkeypatch.setitem(sys.modules, "cv2", fake)

    def partial_dump(doc, output, **kwargs):
        output.write("{")
        raise OSError("simulated manifest write failure")

    monkeypatch.setattr(extractor.json, "dump", partial_dump)
    out = tmp_path / "session.json"
    with pytest.raises(OSError, match="manifest write failure"):
        extractor.extract(tmp_path / "fake.mp4", [{"action_id": "event-2", "timestamp_s": 3.0}], out,
                          recording_id="session-a", split="train", before=1, after=1,
                          interval=1, max_frames=5)
    assert not out.exists()
    assert list(frames.iterdir()) == [keep]
    assert keep.read_bytes() == b"pre-existing"


def test_readme_is_plain_readable_markdown_with_balanced_fences_and_no_wrapper_html():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert readme.startswith("# PoGoBot\n")
    assert readme.count("```") % 2 == 0
    assert not any(tag in readme for tag in ("<div", "</div>", "<picture", "<img", "<sub>"))
    assert "no model accuracy claim" in " ".join(readme.lower().split())
    assert "detector recall-75.9" not in readme
