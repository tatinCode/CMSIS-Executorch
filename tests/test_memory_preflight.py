import contextlib
import copy
import io
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from tools import inference
from tools import inspect_reference_pte as inspector
from tools import memory_preflight as preflight


def npy(data, shape, descr="<f4", fortran=False):
    header = repr({"descr": descr, "fortran_order": fortran, "shape": shape}).encode()
    header += b" " * ((-(10 + len(header) + 1)) % 64) + b"\n"
    return b"\x93NUMPY\x01\x00" + struct.pack("<H", len(header)) + header + data


def vela_stream(scratch=64):
    io_block = struct.pack("<10i", 1, 1, 1, 1, 1, 1, 16, 1, 0, 1)
    blocks = [("vela_bin_stream", b""), ("cmd_data", b"COP1"), ("weight_data", b""),
              ("scratch_size", struct.pack("<I", scratch)), ("inputs", io_block),
              ("outputs", io_block), ("vela_end_stream", b"")]
    return b"".join(name.encode().ljust(16, b"\0") + struct.pack("<4I", len(data), 0, 0, 0)
                    + data + bytes((-len(data)) % 16) for name, data in blocks)


class VelaInspectionTests(unittest.TestCase):
    def test_scratch_is_decoded_from_payload(self):
        self.assertEqual(inspector.inspect_vela_payload(vela_stream())["scratch_bytes"], 64)

    def test_invalid_or_unsupported_streams_are_rejected(self):
        data = vela_stream()
        scratch = data.index(b"scratch_size")
        inputs = data.index(b"inputs\0")
        cases = [data[:-1], data + b"\0", data.replace(b"COP1", b"BAD!"),
                 data.replace(b"scratch_size", b"unknown_size"), vela_stream(0), vela_stream(8)]
        malformed = bytearray(data)
        struct.pack_into("<I", malformed, scratch + 16, 0xFFFFFFFF)
        cases.append(bytes(malformed))
        malformed = bytearray(data)
        struct.pack_into("<i", malformed, inputs + 32 + 36, 2)  # Unsupported I/O region.
        cases.append(bytes(malformed))
        for value in cases:
            with self.subTest(length=len(value)):
                with self.assertRaises(ValueError):
                    inspector.inspect_vela_payload(value)


class MemoryPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.model = root / "model"
        self.reference = self.model / "reference"
        self.reference.mkdir(parents=True)
        self.image = root / "cat.jpg"
        self.image.write_bytes(b"consented local image fixture")
        self.pte = self.model / "mv2_ethosu85_256.pte"
        self.pte.write_bytes(b"model fixture")
        self.methods = [{"name": "forward",
                         "inputs": [{"shape": [1, 3, 224, 224], "dtype": "FLOAT", "dim_order": [0, 1, 2, 3]}],
                         "outputs": [{"shape": [1, 1000], "dtype": "FLOAT", "dim_order": [0, 1]}],
                         "delegates": ["EthosUBackend"], "cpu_operators": [], "planned_buffer_bytes": [33]}]
        self.metadata = {"schema_version": 0, "methods": copy.deepcopy(self.methods), "hardware_verified": False}
        self.metadata_path = self.model / "program_metadata.json"
        self.metadata_path.write_text(json.dumps(self.metadata))
        raw = bytes(preflight.INPUT_BYTES)
        (self.reference / "input.f32").write_bytes(raw)
        (self.reference / "input.npy").write_bytes(npy(raw, (1, 3, 224, 224)))
        self.logits = [float(index) for index in range(1000)]
        (self.reference / "host_logits.npy").write_bytes(npy(struct.pack("<1000f", *self.logits), (1, 1000)))
        self.reference_report = {
            "input_bytes": preflight.INPUT_BYTES,
            "input_sha256": preflight.sha256(self.reference / "input.f32"),
            "source_sha256": preflight.sha256(self.image), "preprocessing": preflight.PREPROCESSING,
            "weights": "MobileNet_V2_Weights.IMAGENET1K_V2",
            "input_npy_sha256": preflight.sha256(self.reference / "input.npy"),
            "host_logits_sha256": preflight.sha256(self.reference / "host_logits.npy"),
            "host_top5": [{"index": index, "label": f"label{index}", "logit": float(index)}
                          for index in range(999, 994, -1)],
        }
        (self.reference / "reference.json").write_text(json.dumps(self.reference_report))
        self.manifest = {
            "model": "mv2", "memory_mode": "Sram_Only", "target": inference.TARGET,
            "executorch_revision": inference.REVISION, "pte_bytes": self.pte.stat().st_size,
            "pte_sha256": preflight.sha256(self.pte), "metadata_sha256": preflight.sha256(self.metadata_path),
            "reference_sha256": preflight.sha256(self.reference / "reference.json"),
        }
        self.save_manifest()
        self.actual = {"schema_version": 0, "methods": copy.deepcopy(self.methods),
                       "pte_bytes": self.manifest["pte_bytes"], "pte_sha256": self.manifest["pte_sha256"],
                       "delegate_memory": [{"method": "forward", "delegate_index": 0, "scratch_bytes": 1024}]}
        self.inspect = mock.patch.object(preflight, "inspect_pte", return_value=self.actual).start()
        self.addCleanup(mock.patch.stopall)
        self.budgets = dict(scratch_bytes=1024, temporary_overhead_bytes=0, arena_bytes=64,
                            sram_bytes=0x400000, mram_bytes=0x200000, firmware_mram_bytes=1024,
                            other_mram_bytes=0, method_pool_bytes=1024, other_sram_bytes=0,
                            other_dtcm_bytes=0, heap_bytes=1024, stack_bytes=1024, dtcm_bytes=0x100000)

    def save_manifest(self):
        (self.model / "manifest.json").write_text(json.dumps(self.manifest))

    def save_metadata(self):
        self.metadata_path.write_text(json.dumps(self.metadata))
        self.manifest["metadata_sha256"] = preflight.sha256(self.metadata_path)
        self.save_manifest()

    def save_reference(self):
        path = self.reference / "reference.json"
        path.write_text(json.dumps(self.reference_report))
        self.manifest["reference_sha256"] = preflight.sha256(path)
        self.save_manifest()

    def run_preflight(self):
        return preflight.preflight(self.model, self.budgets, self.image)

    def test_alignment_rejects_invalid_types_and_boundaries(self):
        for value, expected in ((0, 0), (1, 32), (32, 32), (33, 64)):
            self.assertEqual(preflight.align_up(value), expected)
        for value, alignment in ((-1, 32), (True, 32), (1.5, 32), (1, 3), (1, 0)):
            with self.assertRaises(preflight.PreflightError):
                preflight.align_up(value, alignment)

    def test_valid_artifacts_fit_declared_budgets_only(self):
        report = self.run_preflight()
        self.assertTrue(report["fits"])
        self.assertFalse(report["firmware_link_verified"])
        self.assertFalse(report["hardware_verified"])
        self.assertEqual(report["requirements"]["arena_required_bytes"], 64)
        self.assertEqual(report["requirements"]["scratch_required_bytes"], 1024)
        self.inspect.assert_called_once_with(self.pte)

    def test_missing_scratch_is_rejected_before_inspecting_artifacts(self):
        del self.budgets["scratch_bytes"]
        with self.assertRaises(preflight.PreflightError):
            self.run_preflight()
        self.inspect.assert_not_called()

    def test_invalid_budget_values_and_unknown_fields_are_rejected(self):
        for value in (None, True, -1, 1.5, "1024"):
            self.budgets["scratch_bytes"] = value
            with self.subTest(value=value):
                with self.assertRaises(preflight.PreflightError):
                    self.run_preflight()
        self.budgets["scratch_bytes"] = 1024
        self.budgets["runtime_bytes"] = 1024
        with self.assertRaises(preflight.PreflightError):
            self.run_preflight()

    def test_zero_scratch_or_firmware_storage_is_rejected(self):
        for name in ("scratch_bytes", "firmware_mram_bytes", "method_pool_bytes", "heap_bytes", "stack_bytes"):
            saved = self.budgets[name]
            self.budgets[name] = 0
            with self.subTest(name=name):
                with self.assertRaises(preflight.PreflightError):
                    self.run_preflight()
            self.budgets[name] = saved

    def test_underallocated_scratch_and_temporary_overhead_block_fit(self):
        self.budgets["scratch_bytes"] = 1023
        self.assertFalse(self.run_preflight()["fits"])
        self.budgets["scratch_bytes"] = 1024
        self.budgets["temporary_overhead_bytes"] = 1  # Reserves 32 aligned bytes.
        self.assertFalse(self.run_preflight()["fits"])

    def test_actual_scratch_not_planned_arena_controls_requirement(self):
        self.actual["delegate_memory"][0]["scratch_bytes"] = 1505280
        report = self.run_preflight()
        self.assertFalse(report["fits"])
        self.assertEqual(report["requirements"]["scratch_required_bytes"], 1505280)

    def test_missing_actual_scratch_fails_closed(self):
        self.actual.pop("delegate_memory")
        with self.assertRaises(preflight.PreflightError):
            self.run_preflight()

    def test_larger_than_hardware_capacity_is_not_authorized(self):
        for name, maximum in preflight.LIMITS.items():
            self.budgets[name] = maximum + 1
            with self.assertRaisesRegex(preflight.PreflightError, "layout limit"):
                self.run_preflight()
            self.budgets[name] = maximum

    def test_larger_profile_requires_explicit_selection_and_never_authorizes_programming(self):
        self.budgets["mram_bytes"] = 5238784
        with self.assertRaisesRegex(preflight.PreflightError, "layout limit"):
            self.run_preflight()
        report = preflight.preflight(self.model, self.budgets, self.image, "hp-only-large")
        self.assertTrue(report["fits"])
        self.assertEqual(report["layout"]["mram_base"], "0x80001000")
        self.assertTrue(report["layout"]["reclaims_default_he_and_user_space"])
        self.assertFalse(report["layout"]["programming_authorized"])
        self.budgets["mram_bytes"] += 1
        with self.assertRaisesRegex(preflight.PreflightError, "layout limit"):
            preflight.preflight(self.model, self.budgets, self.image, "hp-only-large")

    def test_unknown_layout_fails_closed(self):
        with self.assertRaises(ValueError):
            preflight.preflight(self.model, self.budgets, self.image, "anything-goes")

    def test_mram_includes_firmware_input_golden_logits_and_other_data(self):
        needed = self.run_preflight()["requirements"]["mram_required_bytes"]
        self.assertEqual(needed, 32 + preflight.INPUT_BYTES + preflight.OUTPUT_BYTES + 1024)
        self.budgets["mram_bytes"] = needed
        self.assertTrue(self.run_preflight()["fits"])
        self.budgets["mram_bytes"] -= 1
        self.assertFalse(self.run_preflight()["fits"])

    def test_reserved_pool_sizes_not_just_minima_count_against_sram(self):
        self.budgets["arena_bytes"] = 2048
        self.budgets["scratch_bytes"] = 2048
        needed = self.run_preflight()["requirements"]["sram_reserved_bytes"]
        self.budgets["sram_bytes"] = needed
        self.assertTrue(self.run_preflight()["fits"])
        self.budgets["sram_bytes"] -= 1
        self.assertFalse(self.run_preflight()["fits"])

    def test_arena_and_dtcm_reservations_are_enforced(self):
        self.budgets["arena_bytes"] = 63
        self.assertFalse(self.run_preflight()["fits"])
        self.budgets["arena_bytes"] = 64
        self.budgets["dtcm_bytes"] = 3071
        self.assertFalse(self.run_preflight()["fits"])
        self.budgets["dtcm_bytes"] = 3072
        self.assertTrue(self.run_preflight()["fits"])

    def test_incompatible_manifest_is_rejected(self):
        for key, value in (("target", "ethos-u55-128"), ("executorch_revision", "different"),
                           ("memory_mode", "Dedicated_Sram"), ("pte_bytes", 0)):
            saved = self.manifest[key]
            self.manifest[key] = value
            self.save_manifest()
            with self.subTest(key=key):
                with self.assertRaises(preflight.PreflightError):
                    self.run_preflight()
            self.manifest[key] = saved
        self.save_manifest()

    def test_hash_mismatches_are_rejected(self):
        self.pte.write_bytes(b"different")
        with self.assertRaises(preflight.PreflightError):
            self.run_preflight()

    def test_finite_logit_corruption_is_rejected_by_recorded_hash(self):
        raw = struct.pack("<1000f", *([0.5] + self.logits[1:]))  # Leaves top-5 unchanged.
        (self.reference / "host_logits.npy").write_bytes(npy(raw, (1, 1000)))
        with self.assertRaisesRegex(preflight.PreflightError, "host_logits.npy hash"):
            self.run_preflight()

    def test_legacy_unbound_references_cannot_claim_fit(self):
        del self.manifest["reference_sha256"]
        self.save_manifest()
        report = self.run_preflight()
        self.assertFalse(report["fits"])
        self.assertIn("hash binding", report["errors"][0])

    def test_wrong_dtype_layout_delegate_or_buffer_size_are_rejected(self):
        base = copy.deepcopy(self.metadata)
        for field, value in (("dtype", "CHAR"), ("dim_order", [0, 2, 3, 1])):
            self.metadata = copy.deepcopy(base)
            self.metadata["methods"][0]["inputs"][0][field] = value
            self.save_metadata()
            with self.assertRaises(preflight.PreflightError):
                self.run_preflight()
        for field, value in (("delegates", []), ("planned_buffer_bytes", [-1]), ("planned_buffer_bytes", [True])):
            self.metadata = copy.deepcopy(base)
            self.metadata["methods"][0][field] = value
            self.save_metadata()
            with self.assertRaises(preflight.PreflightError):
                self.run_preflight()

    def test_cached_metadata_must_match_actual_pte(self):
        self.actual["methods"][0]["planned_buffer_bytes"] = [999]
        with self.assertRaisesRegex(preflight.PreflightError, "actual .pte"):
            self.run_preflight()

    def test_inspection_failure_publishes_blocked_not_stale_success(self):
        output = Path(self.temp.name) / "preflight.json"
        output.write_text('{"fits": true}')
        self.inspect.side_effect = preflight.PreflightError("PTE decoder failed")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(preflight.main(self.cli_args(output)), 1)
        report = json.loads(output.read_text())
        self.assertFalse(report["fits"])
        self.assertIn("PTE decoder failed", report["errors"][0])

    def test_bad_preprocessing_or_wrong_source_image_is_rejected(self):
        self.reference_report["preprocessing"] = {}
        self.save_reference()
        with self.assertRaisesRegex(preflight.PreflightError, "preprocessing"):
            self.run_preflight()
        self.reference_report["preprocessing"] = preflight.PREPROCESSING
        self.save_reference()
        self.image.write_bytes(b"other image")
        with self.assertRaises(preflight.PreflightError):
            self.run_preflight()

    def test_raw_input_and_export_input_must_agree(self):
        raw = bytearray(preflight.INPUT_BYTES)
        struct.pack_into("<f", raw, 0, 1.0)
        (self.reference / "input.npy").write_bytes(npy(raw, (1, 3, 224, 224)))
        self.reference_report["input_npy_sha256"] = preflight.sha256(self.reference / "input.npy")
        self.save_reference()
        with self.assertRaisesRegex(preflight.PreflightError, "export input.npy differs"):
            self.run_preflight()

    def test_invalid_npy_logits_are_rejected(self):
        path = self.reference / "host_logits.npy"
        raw = struct.pack("<1000f", *self.logits)
        cases = [b"logits", npy(raw, (1000,)), npy(raw, (1, 1000), "|O"),
                 npy(raw, (1, 1000), fortran=True), npy(raw, (1, 1000))[:-1],
                 npy(raw, (1, 1000)) + b"x", npy(struct.pack("<f", math.nan) + raw[4:], (1, 1000)),
                 npy(struct.pack("<f", math.inf) + raw[4:], (1, 1000))]
        for data in cases:
            path.write_bytes(data)
            with self.subTest(length=len(data)):
                with self.assertRaises(preflight.PreflightError):
                    preflight.read_float_npy(path, (1, 1000))
                with self.assertRaises(preflight.PreflightError):
                    self.run_preflight()

    def test_host_top5_must_match_logits(self):
        self.reference_report["host_top5"][0]["logit"] = -10
        self.save_reference()
        with self.assertRaisesRegex(preflight.PreflightError, "top-5"):
            self.run_preflight()

    def cli_args(self, output):
        args = [str(self.model), "--image", str(self.image), "--output", str(output)]
        for name, value in self.budgets.items():
            args.extend(["--" + name.replace("_", "-"), str(value)])
        return args

    def test_failed_rerun_replaces_old_success_with_blocked_report(self):
        output = Path(self.temp.name) / "preflight.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(preflight.main(self.cli_args(output)), 0)
            self.assertTrue(json.loads(output.read_text())["fits"])
            self.budgets["scratch_bytes"] = 1
            self.assertEqual(preflight.main(self.cli_args(output)), 1)
            self.assertFalse(json.loads(output.read_text())["fits"])
            self.pte.unlink()
            self.assertEqual(preflight.main(self.cli_args(output)), 1)
            self.assertEqual(json.loads(output.read_text())["status"], "blocked")

    def test_missing_or_malformed_cli_budget_invalidates_old_success(self):
        output = Path(self.temp.name) / "preflight.json"
        for value in (None, "nonsense", "-1"):
            args = self.cli_args(output)
            index = args.index("--scratch-bytes")
            if value is None:
                del args[index:index + 2]
            else:
                args[index + 1] = value
            output.write_text('{"fits": true}')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(preflight.main(args), 1)
            self.assertFalse(json.loads(output.read_text())["fits"])

    def test_output_cannot_overwrite_artifacts_or_follow_symlinks(self):
        for output in (self.model / "manifest.json", self.metadata_path, self.reference / "reference.json"):
            original = output.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                preflight.main(self.cli_args(output))
            self.assertEqual(output.read_bytes(), original)
        link = Path(self.temp.name) / "report.json"
        link.symlink_to(self.model / "manifest.json")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            preflight.main(self.cli_args(link))


if __name__ == "__main__":
    unittest.main()
