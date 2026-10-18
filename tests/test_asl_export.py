import argparse
import copy
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from tools import inspect_asl_pte as inspector

try:
    from ml.asl import export
    from ml.asl.camera_data import CAMERA_CLASSES
    from ml.asl.model import contract
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


def method_fixture():
    return dict(name="forward", delegates=["EthosUBackend"],
                inputs=[dict(shape=[1, 3, 128, 128], dtype="FLOAT", dim_order=[0, 1, 2, 3])],
                outputs=[dict(shape=[1, 7], dtype="FLOAT", dim_order=[0, 1])],
                cpu_operators=[dict(name=n, overload=o) for n, o in inspector.CPU_OPERATORS],
                planned_buffer_bytes=[245760])


class AslInspectionTests(unittest.TestCase):
    def test_exact_shape_delegate_and_existing_boundary_kernels_required(self):
        method = method_fixture()
        inspector.validate_methods([method])
        changes = [dict(name="other"), dict(delegates=[]), dict(delegates=["EthosUBackend"] * 2),
                   dict(cpu_operators=[]), dict(planned_buffer_bytes=[-1]), dict(planned_buffer_bytes=[True])]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                inspector.validate_methods([{**method, **change}])
        for field, key, value in (("inputs", "shape", [1, 3, 224, 224]),
                                  ("outputs", "shape", [1, 6]), ("inputs", "dtype", "CHAR"),
                                  ("inputs", "dim_order", [0, 2, 3, 1])):
            changed = copy.deepcopy(method)
            changed[field][0][key] = value
            with self.subTest(field=field, key=key), self.assertRaises(ValueError):
                inspector.validate_methods([changed])

    def test_memory_keeps_the_proven_reservations_and_detects_two_model_overflow(self):
        from tools import stored_image_firmware as stored
        metadata = dict(methods=[method_fixture()], delegate_memory=[dict(scratch_bytes=491520)], pte_bytes=2500000)
        report = inspector.memory_report(metadata, dict(pte_bytes=3835520))
        self.assertTrue(report["fits_existing_runtime_pools"])
        self.assertEqual(report["arena_reserved_bytes"], stored.ARENA)
        self.assertEqual(report["temporary_reserved_bytes"], stored.TEMPORARY)
        self.assertFalse(report["two_models_alone_fit_mram"])
        metadata["delegate_memory"][0]["scratch_bytes"] = stored.TEMPORARY
        self.assertFalse(inspector.memory_report(metadata, dict(pte_bytes=3835520))["fits_existing_runtime_pools"])


@unittest.skipUnless(HAS_TORCH, "Use the ASL or inference environment")
class AslExportTests(unittest.TestCase):
    def test_scale_is_extracted_from_the_final_output_not_an_internal_node(self):
        import torch
        def graph(target='quantized_decomposed.dequantize_per_tensor.default', scale=0.125):
            node = SimpleNamespace(target=target, args=(None, scale, 7, -128, 127, torch.int8))
            return SimpleNamespace(graph=SimpleNamespace(nodes=[SimpleNamespace(op='output', args=((node,),))]))
        self.assertEqual(export.final_output_scale(graph()), 0.125)
        for obj in (graph(target='aten.linear.default'), graph(scale=0), graph(scale=float('nan')),
                    SimpleNamespace(graph=SimpleNamespace(nodes=[]))):
            with self.assertRaises(ValueError):
                export.final_output_scale(obj)

    def test_existing_output_refused_before_any_export_or_hardware_work(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "new ASL export output"):
                export.run(argparse.Namespace(output=Path(directory)))

    def test_candidate_must_be_seven_class_and_bound_to_the_reviewed_split(self):
        checkpoint = dict(schema=2, classes=list(CAMERA_CLASSES), preprocessing=contract(128),
                          configuration=dict(camera_manifest_sha256="camera", label_review_sha256="review"))
        completed = dict(checkpoint_sha256="checkpoint", final_test_evaluated=False)
        export.validate_candidate(checkpoint, completed, "checkpoint", "camera", "review")
        for args in (({**checkpoint, "schema": 1}, completed, "checkpoint", "camera", "review"),
                     (checkpoint, completed, "changed", "camera", "review"),
                     (checkpoint, completed, "checkpoint", "changed", "review"),
                     (checkpoint, {**completed, "final_test_evaluated": True}, "checkpoint", "camera", "review")):
            with self.assertRaises(ValueError):
                export.validate_candidate(*args)

    def test_calibration_excludes_validation_and_final_test(self):
        records = [dict(path=f"train/{c}.png", split="train", label=c) for c in CAMERA_CLASSES]
        records += [dict(path="val/A.png", split="val", label="A")]
        rehearsal = [dict(path="source/A.png", split="train", label="A")]
        selected = dict(camera_train=records[:-1], camera_val=records[-1:], rehearsal_train=rehearsal)
        source = dict(records=rehearsal)
        train, val, replay = export.calibration_records(records, selected, source)
        self.assertEqual(len(train), 7)
        self.assertEqual(val, records[-1:])
        self.assertEqual(replay, rehearsal)
        for split in ("val", "test"):
            bad = [dict(path="source/A.png", split=split, label="A")]
            with self.subTest(split=split), self.assertRaisesRegex(ValueError, "TRAIN rows"):
                export.calibration_records(records, {**selected, "rehearsal_train": bad}, dict(records=bad))
        with self.assertRaisesRegex(ValueError, "current reviewed camera audit"):
            export.calibration_records(records, {**selected, "camera_train": records}, source)

    def test_quantization_gate_cannot_trade_safety_for_coverage(self):
        original = dict(correct_letter_count=13, wrong_letter_count=1, negative_false_letter_count=0)
        self.assertTrue(export.quantization_gate(original, original))
        for changes in (dict(correct_letter_count=12), dict(wrong_letter_count=2),
                        dict(correct_letter_count=20, negative_false_letter_count=1)):
            with self.subTest(changes=changes):
                self.assertFalse(export.quantization_gate(original, {**original, **changes}))


if __name__ == "__main__":
    unittest.main()
