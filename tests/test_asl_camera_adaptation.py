import argparse
from collections import Counter
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest import mock
import zlib

try:
    import torch
    from torch import nn
    from ml.asl import adapt, camera_data, evaluate
    from ml.asl.data import CLASSES
    from ml.asl.model import ADAPTED_CLASSES, contract, load_checkpoint, make_model
    from ml.asl.train import metrics
    from tools.capture_camera import save_capture
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


@unittest.skipUnless(HAS_TORCH, "Run with the ASL environment")
class CameraAuditTests(unittest.TestCase):
    def capture(self, root, session="session1", label="A", seed=1):
        payload = random.Random(seed).randbytes(128 * 128 * 3)
        metadata = dict(frame=7, crc32=f"{zlib.crc32(payload):08x}", format="RGB888P", width=128, height=128,
                        capture_protocol=4, wire_version=2, source_width=480, source_height=480,
                        crop="none", resize="PIL_bilinear_22bit")
        return save_capture(root, session, label, payload, metadata, b"test log")

    def test_inventory_verifies_hashes_and_maps_unknown_to_other(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.capture(root, label="UNKNOWN")
            records = camera_data.inventory_session(root, "session1", "train")
            self.assertEqual(records[0]["label"], "OTHER")
            self.assertEqual(records[0]["capture_label"], "UNKNOWN")
            self.assertEqual(records[0]["split"], "train")

    def test_corrupt_raw_png_metadata_and_incomplete_groups_are_rejected(self):
        for kind in ("raw", "png", "session", "label", "split", "missing-log", "orphan", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = self.capture(root)
                if kind == "raw":
                    path.with_suffix(".rgb").write_bytes(bytes(49152))
                elif kind == "png":
                    path.write_bytes(b"corrupt")
                elif kind in ("session", "label", "split"):
                    metadata = json.loads(path.with_suffix(".json").read_text())
                    metadata[kind] = "changed"
                    path.with_suffix(".json").write_text(json.dumps(metadata))
                elif kind == "missing-log":
                    path.with_suffix(".log").unlink()
                elif kind == "orphan":
                    (path.parent / "orphan.png").write_bytes(b"orphan")
                else:
                    (path.parent / "alias.png").symlink_to(path)
                with self.assertRaises(ValueError):
                    camera_data.inventory_session(root, "session1", "train")

    def fixture_records(self):
        return [dict(path=f"{split}/{c}.png", label=c, session=f"session-{split}", split=split,
                     normalized_sha256=f"{split}:{c}", dhash=random.Random(f"{split}:{c}").getrandbits(64),
                     sha256=f"sha:{split}:{c}")
                for split in ("train", "val") for c in CAMERA_CLASSES]

    def test_session_assignments_are_whole_and_disjoint(self):
        records = self.fixture_records()
        retained, quarantine = camera_data.split_inventory(records)
        self.assertEqual(retained, records)
        self.assertFalse(quarantine)
        records[-1]["session"] = "session-train"
        with self.assertRaisesRegex(ValueError, "session cannot cross"):
            camera_data.split_inventory(records)

    def test_cross_split_near_match_quarantined_not_recycled_into_training(self):
        records = self.fixture_records()
        near = copy.deepcopy(records[7])
        near.update(path="val/near.png", dhash=records[0]["dhash"] ^ 3, normalized_sha256="unique-near")
        records.append(near)
        retained, quarantine = camera_data.split_inventory(records)
        self.assertNotIn(near, retained)
        self.assertEqual(quarantine[0]["path"], "val/near.png")
        self.assertEqual(quarantine[0]["split"], "val")
        self.assertEqual(sum(r["split"] == "train" for r in retained), 7)

    def test_duplicate_conflicting_labels_and_missing_class_fail(self):
        records = self.fixture_records()
        records[-1]["normalized_sha256"] = records[0]["normalized_sha256"]
        with self.assertRaisesRegex(ValueError, "conflicting labels"):
            camera_data.split_inventory(records)
        records = self.fixture_records()
        records[7]["dhash"] = records[0]["dhash"]
        with self.assertRaisesRegex(ValueError, "Missing val classes"):
            camera_data.split_inventory(records)

    def test_review_is_hash_bound_and_cannot_silently_remove_a_class(self):
        records = self.fixture_records()
        review = dict(schema=1, camera_manifest_sha256="manifest", status="reviewed", excluded=[])
        self.assertEqual(adapt.reviewed_records(records, review, "manifest"), records)
        with self.assertRaisesRegex(ValueError, "matching completed"):
            adapt.reviewed_records(records, review, "changed")
        review["excluded"] = [dict(path=records[0]["path"], sha256="wrong", reason="label")]
        with self.assertRaisesRegex(ValueError, "does not match"):
            adapt.reviewed_records(records, review, "manifest")
        review["excluded"][0]["sha256"] = records[0]["sha256"]
        with self.assertRaisesRegex(ValueError, "Missing reviewed train"):
            adapt.reviewed_records(records, review, "manifest")

    def test_audit_rechecks_captures_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "camera"
            for i, label in enumerate((*CLASSES, "UNKNOWN")):
                self.capture(root, "train", label, seed=i)
                self.capture(root, "val", label, seed=i+100)
            output = Path(directory) / "audit"
            args = argparse.Namespace(root=root, output=output, train_session=["train"], val_session=["val"])
            with redirect_stdout(io.StringIO()):
                camera_data.prepare(args)
            path = output / "manifest.json"
            camera_data.write_json(output / "review.json", dict(schema=1, camera_manifest_sha256=adapt.sha256(path),
                                   status="reviewed", excluded=[]))
            _, records, _, _ = adapt.load_camera_data(path, output / "review.json")
            self.assertEqual(len(records), 14)
            with self.assertRaisesRegex(ValueError, "new audit output"):
                camera_data.prepare(args)
            extra = self.capture(root, "val", "A", seed=999)
            self.assertTrue(extra.exists())
            with self.assertRaisesRegex(ValueError, "audit assignments changed"):
                adapt.load_camera_data(path, output / "review.json")


@unittest.skipUnless(HAS_TORCH, "Run with the ASL environment")
class AdaptationModelTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)

    def test_other_head_preserves_six_rows_and_freezes_backbone(self):
        model = make_model()
        weight, bias = model.classifier[1].weight.detach().clone(), model.classifier[1].bias.detach().clone()
        old = adapt.initialize_other_head(model)
        self.assertEqual(model.classifier[1].out_features, 7)
        self.assertTrue(torch.equal(model.classifier[1].weight[:6], weight))
        self.assertTrue(torch.equal(model.classifier[1].bias[:6], bias))
        self.assertTrue(torch.equal(old[1].weight, weight))
        self.assertFalse(any(p.requires_grad for p in model.features.parameters()))
        self.assertTrue(all(p.requires_grad for p in model.classifier.parameters()))
        self.assertFalse(model.features.training)
        self.assertEqual(CAMERA_CLASSES, ADAPTED_CLASSES)

    def test_schema_two_roundtrip_and_class_schema_mismatch_rejected(self):
        model = make_model(classes=ADAPTED_CLASSES).eval()
        checkpoint = dict(schema=2, architecture="mobilenet_v2", classes=list(ADAPTED_CLASSES),
                          preprocessing=contract(128), state_dict=model.state_dict())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.pth"
            torch.save(checkpoint, path)
            reloaded, _ = load_checkpoint(path)
            self.assertTrue(torch.equal(model.classifier[1].weight, reloaded.classifier[1].weight))
            checkpoint["schema"] = 1
            torch.save(checkpoint, path)
            with self.assertRaisesRegex(ValueError, "label order"):
                load_checkpoint(path)
            checkpoint["schema"] = 2
            checkpoint["classes"][-1] = "UNKNOWN"
            torch.save(checkpoint, path)
            with self.assertRaisesRegex(ValueError, "label order"):
                load_checkpoint(path)

    def test_sampler_balances_classes_within_separate_training_domains(self):
        targets = torch.tensor([0, 0, 1, 6, 0, 1, 1, 1])
        weights = adapt.sampling_weights(targets, 4, .75)
        self.assertAlmostEqual(float(weights[:4].sum()), .75)
        self.assertAlmostEqual(float(weights[4:].sum()), .25)
        for c in (0, 1, 6):
            self.assertAlmostEqual(float(weights[:4][targets[:4] == c].sum()), .25)
        for c in (0, 1):
            self.assertAlmostEqual(float(weights[4:][targets[4:] == c].sum()), .125)
        with self.assertRaises(ValueError):
            adapt.sampling_weights(targets, len(targets), .75)

    def test_decisions_distinguish_raw_top1_displayed_letters_and_learned_rejection(self):
        logits = torch.zeros(5, 7)
        logits[0, 6] = 20  # OTHER is confidently learned, never displayed.
        logits[1, 5] = 20  # NO_HAND is not a letter.
        logits[2, 4] = 20  # A confident wrong Y for an L.
        logits[3, 3] = 1   # L top-1 but below threshold, not a recognized L.
        logits[4, 0] = 20  # OTHER confidently mislabeled A.
        report, rows = adapt.decisions(logits, ["OTHER", "NO_HAND", "L", "L", "OTHER"], CAMERA_CLASSES, .8)
        self.assertEqual(rows[0]["prediction"], "UNKNOWN")
        self.assertEqual(rows[0]["top_class"], "OTHER")
        self.assertEqual([r["display"] for r in rows], ["--", "--", "Y", "--", "A"])
        self.assertEqual(report["letter_count"], 2)
        self.assertEqual(report["correct_letter_count"], 0)
        self.assertEqual(report["wrong_letter_count"], 1)
        self.assertEqual(report["suppressed_letter_count"], 1)
        self.assertEqual(report["negative_false_letter_count"], 1)
        self.assertEqual(report["per_class"]["L"]["top1_correct_count"], 1)
        self.assertEqual(report["per_class"]["L"]["correct_display_count"], 0)

    def test_baseline_comparison_uses_the_same_negative_behavior_denominator(self):
        logits = torch.zeros(2, 6)
        logits[:, 0] = 20
        report, _ = adapt.decisions(logits, ["A", "OTHER"], CLASSES, .8)
        self.assertEqual(report["count"], 2)
        self.assertEqual(report["raw_class_metrics"]["count"], 1)
        self.assertEqual(report["negative_false_letter_rate"], 1.)
        self.assertEqual(report["displayed_letter_accuracy"], .5)

    def test_metrics_support_other_without_changing_default_six_class_contract(self):
        logits = torch.zeros(2, 7)
        logits[0, 6] = 20
        logits[1, 3] = 20
        report = metrics(logits, torch.tensor([6, 3]), .8, CAMERA_CLASSES)
        self.assertEqual(report["per_class_recall"]["OTHER"], 1.)
        self.assertEqual(report["per_class_support"]["OTHER"], 1)
        with self.assertRaises(ValueError):
            metrics(logits, torch.tensor([6, 3]), .8)

    def test_existing_image_evaluator_supports_learned_other(self):
        from PIL import Image
        class AlwaysOther(nn.Module):
            def forward(self, images):
                result = torch.zeros(len(images), 7)
                result[:, 6] = 20
                return result
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for label in ("A", "UNKNOWN", "OTHER"):
                (root / label).mkdir()
                Image.new("RGB", (128, 128), "red").save(root / label / "test.png")
            checkpoint = root / "model.pth"
            checkpoint.write_bytes(b"mocked checkpoint")
            args = argparse.Namespace(output=None, threads=1, checkpoint=checkpoint, image=[], folder=root,
                                      seed=2026, limit_per_class=0, unknown_root=None, batch_size=8, threshold=.8)
            output = io.StringIO()
            with mock.patch.object(evaluate, "load_checkpoint", return_value=(AlwaysOther(),
                                   dict(classes=list(CAMERA_CLASSES), preprocessing=contract(128)))), redirect_stdout(output):
                evaluate.evaluate(args)
            report = json.loads(output.getvalue())
            self.assertEqual(report["unknown_count"], 2)
            self.assertEqual(report["unknown_rejection_rate"], 1.)
            self.assertEqual(report["unknown_false_letter_rate"], 0.)
            self.assertEqual(report["all_class_metrics"]["per_class_support"]["OTHER"], 2)
            self.assertEqual(report["known_metrics"]["count"], 1)

    def test_old_threshold_study_refuses_seven_class_checkpoint(self):
        from ml.asl import rejection
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(output=Path(directory) / "new", checkpoint=Path("mock"))
            with mock.patch.object(rejection, "load_checkpoint", return_value=(None,
                                   dict(schema=2, classes=list(CAMERA_CLASSES)))):
                with self.assertRaisesRegex(ValueError, "six-class baseline"):
                    rejection.run(args)


if HAS_TORCH:
    CAMERA_CLASSES = camera_data.CAMERA_CLASSES


if __name__ == "__main__":
    unittest.main()
