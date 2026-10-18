import tempfile
import random
from pathlib import Path
import unittest

try:
    import torch
    from PIL import Image
    from ml.asl.model import contract, load_checkpoint, make_model, transform
    from ml.asl.train import metrics, validate_manifest
    from ml.asl.data import CLASSES
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


@unittest.skipUnless(HAS_TORCH, "Run with .work/asl/venv/bin/python for model tests")
class ASLModelTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)

    def test_rgb_preprocessing_known_pixels_and_center_crop(self):
        image = Image.new("RGB", (12, 8), "red")
        # Outer columns must be cropped away.
        for x in (0, 1, 10, 11):
            for y in range(8):
                image.putpixel((x, y), (0, 255, 0))
        result = transform(96)(image)
        self.assertEqual(tuple(result.shape), (3, 96, 96))
        expected = torch.tensor([(1 - .485) / .229, - .456 / .224, - .406 / .225])
        torch.testing.assert_close(result, expected[:, None, None].expand_as(result))

    def test_checkpoint_roundtrip_and_reject_wrong_class_order(self):
        model = make_model().eval()
        sample = torch.rand(1, 3, 96, 96)
        checkpoint = dict(schema=1, architecture="mobilenet_v2", classes=list(CLASSES),
                          preprocessing=contract(96), state_dict=model.state_dict())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model.pth"
            torch.save(checkpoint, path)
            loaded, _ = load_checkpoint(path)
            with torch.inference_mode():
                torch.testing.assert_close(model(sample), loaded(sample))
            checkpoint["classes"].reverse()
            torch.save(checkpoint, path)
            with self.assertRaisesRegex(ValueError, "label order"):
                load_checkpoint(path)

    def test_already_resized_full_view_preserves_normalized_input(self):
        image = Image.frombytes("RGB", (480, 480), random.Random(2026).randbytes(480 * 480 * 3))
        resized = image.resize((128, 128), Image.Resampling.BILINEAR)
        self.assertTrue(torch.equal(transform(128)(image), transform(128)(resized)))

    def test_metrics_report_rejection_separately_from_accuracy(self):
        logits = torch.tensor([[10., 0, 0, 0, 0, 0], [0., 0, 0, 0, 0, 0]])
        report = metrics(logits, torch.tensor([0, 1]), .8)
        self.assertEqual(report["accuracy"], .5)
        self.assertEqual(report["accepted_accuracy"], 1.)
        self.assertEqual(report["coverage"], .5)
        self.assertEqual(report["confusion_matrix_rows_actual_columns_predicted"][1][0], 1)
        self.assertEqual(report["balanced_accuracy"], .5)
        self.assertIsNone(report["per_class_recall"]["C"])
        self.assertEqual(report["per_class_support"]["C"], 0)

    def test_reject_cross_split_group(self):
        records = [dict(path=f"{s}/{c}.jpg", label=c, split=s, pixels_sha256=f"{s}/{c}", group=f"{s}/{c}")
                   for s in ("train", "val", "test") for c in CLASSES]
        manifest = dict(schema=1, classes=list(CLASSES), records=records)
        validate_manifest(manifest)
        records[6]["group"] = records[0]["group"]
        with self.assertRaisesRegex(ValueError, "leakage"):
            validate_manifest(manifest)

    def test_rejection_calibration_refuses_confident_unknowns_without_sacrificing_coverage(self):
        import numpy as np
        from ml.asl.rejection import choose_threshold
        known = np.array([[.9, .02, .02, .02, .02, .02]])
        unknown = np.array([[.99, .002, .002, .002, .002, .002]])
        selected, tradeoff, _ = choose_threshold(known, np.array([0]), unknown)
        self.assertIsNone(selected)
        djalsdjsakjadkj


if __name__ == "__main__":
    unittest.main()
