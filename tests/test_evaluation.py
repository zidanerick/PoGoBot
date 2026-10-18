import json
from pathlib import Path

import pytest

from pogobot.evaluation import Box, evaluate_frames, evaluate_outcomes, load_manifest
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
