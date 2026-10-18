import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import inference
from tools import prepare_camera_inference as prepare
from tools import prepare_stored_image as stored
import test_prepare_stored_image as package_guards
import test_camera_inference_firmware as camera_layout


class PrepareCameraInferenceTests(package_guards.PrepareStoredImageTests):
    """Apply the existing package/map/TOC corruption tests to the camera-specific helper."""

    def setUp(self):
        context = mock.patch.object(package_guards, "prepare", prepare)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def test_cli_has_no_program_command_or_write_flag(self):
        for args in (("program", "--write"), ("review", "--write"),
                     ("prepare", "--setools-root", "/unused", "--image", "/unused", "--hp")):
            result = subprocess.run([sys.executable, "-m", "tools.prepare_camera_inference", *args],
                                    cwd=inference.ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)

    def test_standalone_map_is_not_accepted_as_camera_package_and_vice_versa(self):
        text, package, device = package_guards.package_fixture()
        with self.assertRaises(inference.SetupError):
            stored.verify_package(text, package, 1984, device)
        with self.assertRaises(inference.SetupError):
            prepare.verify_package(text.replace(prepare.IMAGE_NAME, stored.IMAGE_NAME), package, 1984, device)

    def test_tool_dependencies_outside_build_are_bound_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "alif").mkdir()
            helper = root / "alif/dependency.py"
            helper.write_text("version one")
            before = prepare.package_fingerprints(root)
            self.assertIn("alif/dependency.py", before)
            helper.write_text("version two")
            self.assertNotEqual(prepare.package_fingerprints(root), before)
            (root / "alias").symlink_to(helper)
            with self.assertRaises(inference.SetupError):
                prepare.package_fingerprints(root)

    def test_foreign_receipt_is_rejected_before_any_build_or_device_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "latest.json").write_text(json.dumps({"receipt": "receipt.json"}))
            (root / "receipt.json").write_text(json.dumps({"format": 1, "target": "stored-image.debug+DevKit-E8-HP"}))
            with mock.patch.object(prepare, "PROGRAM", root), mock.patch.object(prepare, "check_build") as check:
                with self.assertRaises(inference.SetupError):
                    prepare.load_prepared()
                check.assert_not_called()

    def test_prepare_isolated_from_both_standalone_pointers_and_detects_late_build_change(self):
        for late_change in (False, True):
            with self.subTest(late_change=late_change), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                installed = root / "installed"
                for folder in ("build/config", "build/images", "build/logs", "utils", "alif"):
                    (installed / folder).mkdir(parents=True, exist_ok=True)
                for name in ("app-gen-toc", "app-write-mram", "app-sign-image"):
                    (installed / name).write_text("mock executable")
                    (installed / name).chmod(0o700)
                (installed / "version.txt").write_text("1.112.00")
                (installed / "utils/global-cfg.db").write_text(json.dumps({
                    "DEVICE": {"Part#": "E8 AE822FA0E5597LS0", "Revision": "A0"}, "MRAM-BURNER": {"Interface": "isp"}}))
                (installed / "build/config/app-device-config.json").write_text("{}")
                (installed / "alif/dependency.py").write_text("unchanged dependency")
                binary = root / "candidate.bin"
                binary.write_bytes(bytes(1984))
                info = {"binary": binary, "binary_sha256": inference.sha256(binary), "binary_bytes": 1984,
                        "build_manifest_sha256": "manifest identity", "image": str(root / "cat.jpg")}
                for folder in ("smoke", "stored", "camera"):
                    (root / folder).mkdir()
                    (root / folder / "latest.json").write_text("keep existing pointer")

                def generate(command, cwd, log):
                    self.assertEqual(command, [cwd / "app-gen-toc", "-f", prepare.CONFIG_NAME])
                    text, package, device = package_guards.package_fixture()
                    (cwd / "build/AppTocPackage.bin").write_bytes(package)
                    (cwd / "build/app-package-map.txt").write_text(text)
                    (cwd / "build/images/app-device-config.bin").write_bytes(device)

                with mock.patch.object(prepare, "PROGRAM", root / "camera"), \
                     mock.patch.object(prepare.accepted, "PROGRAM", root / "stored"), \
                     mock.patch.object(prepare.shared, "PROGRAM", root / "smoke"), \
                     mock.patch.object(prepare, "baseline_config", side_effect=package_guards.baseline), \
                     mock.patch.object(prepare, "check_build", side_effect=[info, {**info, "binary_sha256": "changed"} if late_change else info, info, info]), \
                     mock.patch.object(prepare.common, "check_space"), \
                     mock.patch.object(prepare.shared, "run_logged", side_effect=generate) as execute, \
                     contextlib.redirect_stdout(io.StringIO()):
                    original = prepare.shared.protected_state(installed)
                    if late_change:
                        with self.assertRaises(inference.SetupError):
                            prepare.prepare(installed, root / "cat.jpg", True)
                        self.assertFalse((root / "camera/latest.json").exists())
                    else:
                        prepare.prepare(installed, root / "cat.jpg", True)
                        receipt, stage = prepare.load_prepared()
                        self.assertEqual(receipt["target"], prepare.TARGET)
                        dependency = stage / "setools/alif/dependency.py"
                        dependency.write_text("changed helper outside build")
                        with self.assertRaises(inference.SetupError):
                            prepare.load_prepared()
                    self.assertEqual(original, prepare.shared.protected_state(installed))
                    for folder in ("smoke", "stored"):
                        self.assertEqual((root / folder / "latest.json").read_text(), "keep existing pointer")
                    execute.assert_called_once()


class CameraBuildValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.work = self.root / ".work/inference/camera-inference-firmware"
        self.vendor = self.root / "firmware/vendor/alif_viewfinder"
        self.baseline = self.root / ".work/inference/stored-image-firmware"
        self.pack = self.root / "packs/AlifSemiconductor/Ensemble/2.2.0"
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        for obj, name, value in ((prepare, "ROOT", self.root), (prepare, "FIRMWARE", self.work),
                                  (prepare.firmware.smoke, "VENDOR", self.vendor),
                                  (prepare.firmware.image, "WORKSPACE", self.baseline)):
            self.patches.enter_context(mock.patch.object(obj, name, value))
        self.symbols, data = camera_layout.CameraInferenceFirmwareTests().fixture()
        self.binary = self.work / "out/camera-inference/DevKit-E8-HP/debug/camera-inference.bin"
        self.write(self.binary, data)
        self.write(self.binary.with_suffix(".elf"), b"test elf")
        self.commands = self.binary.parent / "compile_commands.json"
        self.write(self.commands, b"[]")
        paths = [self.root / "firmware/camera_inference/src" / name for name in ("main.cpp", "power.cpp", "preview.cpp", "frame_export.cpp")]
        paths += [self.root / "firmware/stored_image/src/model_runner.cpp", self.root / "firmware/smoke/src/platform.cpp",
                  self.work / "generated/platform.cpp"]
        paths += [self.vendor / "viewfinder/camera" / name for name in ("isp_calibration.c", "isp_calibration_mt9m114.c", "isp_calibration.h")]
        for folder in ("firmware/camera_inference/include", "firmware/smoke/include", "firmware/stored_image/include"):
            paths.append(self.root / folder / "test.h")
        for path in paths:
            self.write(path, b"source input")
        for name in prepare.TOOLS:
            self.write(self.root / "tools" / name, b"build helper")
        for name in ("image_config", "reference_model_pte", "reference_input", "reference_logits"):
            self.write(self.work / f"assets/{name}.h", b"generated asset")
        self.write(self.work / "RTE/test.h", b"RTE config")
        for name in prepare.PROJECTS:
            self.write(self.work / name, b"project config")
        linker = self.pack / "Device/core/rtss_hp/linker/linker_gnu_mram.ld.src"
        isp = self.pack / "libs/isp/libisp_gcc.a"
        self.write(linker, b"SDK linker")
        self.write(isp, b"ISP library")
        header = self.pack / "Alif_CMSIS/Include/Driver_ISP.h"
        self.write(header, b"SDK interface")
        self.original = self.baseline / "baseline.bin"
        self.write(self.original, data)
        self.info = {"binary": self.original, "build_manifest_sha256": "baseline manifest"}
        self.meta = {
            "target": prepare.TARGET, "layout_profile": prepare.PROFILE.report(), "executorch_revision": inference.REVISION,
            "hardware_verified": False, "programming_authorized": False, "camera_classification_implemented": False,
            "capture": prepare.firmware.CAPTURE,
            "bin": str(self.binary.relative_to(self.root)), "elf": str(self.binary.with_suffix(".elf").relative_to(self.root)),
            "bin_sha256": inference.sha256(self.binary), "elf_sha256": inference.sha256(self.binary.with_suffix(".elf")),
            "generator_sha256": inference.sha256(Path(prepare.firmware.__file__)), "compile_commands_sha256": inference.sha256(self.commands),
            "tool_sha256": {name: inference.sha256(self.root / "tools" / name) for name in prepare.TOOLS},
            "source_sha256": {str(path.relative_to(self.root)): inference.sha256(path) for path in paths},
            "generated_sha256": {str(path.relative_to(self.work)): inference.sha256(path) for folder in ("assets", "RTE", "generated")
                                 for path in (self.work / folder).rglob("*") if path.is_file()},
            "project_sha256": {name: inference.sha256(self.work / name) for name in prepare.PROJECTS},
            "stored_image_build_manifest_sha256": self.info["build_manifest_sha256"],
            "model_sha256": "model identity", "reference_hashes": {"reference": "identity"}, "driver_revision": "driver revision",
            "comparison": {"max_abs_logit_error": 1.0, "min_cosine": 0.98, "top1_must_match": True},
            "vendor_revision": "vendor revision", "sdk_linker_sha256": inference.sha256(linker), "isp_library_sha256": inference.sha256(isp),
            "pack_input_sha256": {str(path.relative_to(self.pack)): inference.sha256(path) for path in (linker, isp, header)},
            "layout": prepare.firmware.verify_layout(self.symbols, data, 3835520),
        }
        baseline = {name: self.meta[name] for name in ("model_sha256", "reference_hashes", "driver_revision", "comparison")}
        baseline["layout"] = self.meta["layout"]
        self.write(self.baseline / "manifest.json", json.dumps(baseline).encode())
        self.save()
        self.patches.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.check_baseline = self.patches.enter_context(mock.patch.object(prepare.accepted, "check_build", return_value=self.info))
        self.patches.enter_context(mock.patch.object(prepare.common, "find_cmsis", return_value=self.root / "packs/ARM/CMSIS/6.1.0"))
        self.patches.enter_context(mock.patch.object(prepare.common, "find_toolchain", return_value=Path("/unused")))
        self.patches.enter_context(mock.patch.object(prepare.common, "run", side_effect=lambda command, **kw:
            "vendor revision" if str(command[0]) == "git" else "Tag_ABI_enum_size: small\nTag_ABI_VFP_args: VFP registers"))
        self.patches.enter_context(mock.patch.object(prepare.firmware.smoke, "parse_symbols", return_value=self.symbols))

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def save(self):
        self.write(self.work / "manifest.json", json.dumps(self.meta).encode())

    def check(self):
        return prepare.check_build(self.root / "cat.jpg")

    def test_valid_build_is_rechecked_with_fresh_camera_layout_and_prerequisite(self):
        info = self.check()
        self.assertEqual(info["binary"], self.binary)
        self.assertEqual(info["binary_sha256"], inference.sha256(self.binary))
        self.check_baseline.assert_called_once()

    def test_other_targets_profiles_and_hardware_claims_are_rejected(self):
        for field, value in (("target", "stored-image.debug+DevKit-E8-HP"), ("layout_profile", {}),
                             ("hardware_verified", True), ("programming_authorized", True), ("camera_classification_implemented", True),
                             ("capture", {"protocol": 3})):
            original = self.meta[field]
            self.meta[field] = value
            self.save()
            with self.subTest(field=field), self.assertRaises(inference.SetupError):
                self.check()
            self.meta[field] = original
        self.save()

    def test_changed_artifacts_sources_generated_config_projects_tools_and_sdk_fail(self):
        paths = [self.binary, self.binary.with_suffix(".elf"), self.commands,
                 self.root / "firmware/camera_inference/src/preview.cpp", self.work / "generated/platform.cpp",
                 self.work / "assets/image_config.h", self.work / "RTE/test.h", self.work / "camera-inference.cproject.yml",
                 self.root / "tools/firmware_layouts.py", self.pack / "Alif_CMSIS/Include/Driver_ISP.h",
                 self.pack / "libs/isp/libisp_gcc.a"]
        for path in paths:
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            with self.subTest(path=path), self.assertRaises(inference.SetupError):
                self.check()
            path.write_bytes(original)

    def test_missing_recorded_identities_or_required_generated_assets_fail(self):
        for group in ("source_sha256", "generated_sha256", "project_sha256", "tool_sha256", "pack_input_sha256"):
            original = self.meta[group]
            self.meta[group] = {}
            self.save()
            with self.subTest(group=group), self.assertRaises(inference.SetupError):
                self.check()
            self.meta[group] = original
        path = self.work / "assets/reference_model_pte.h"
        path.unlink()
        del self.meta["generated_sha256"]["assets/reference_model_pte.h"]
        self.save()
        with self.assertRaises(inference.SetupError):
            self.check()

    def test_foreign_binary_paths_or_workspace_escapes_fail(self):
        for value in ("/outside/camera-inference.bin", str(self.original),
                      str(self.work / "other.bin")):
            self.meta["bin"] = value
            self.save()
            with self.subTest(value=value), self.assertRaises(inference.SetupError):
                self.check()

    def test_changed_prerequisite_reference_thresholds_revision_or_layout_fail(self):
        for field, value in (("stored_image_build_manifest_sha256", "changed"), ("comparison", {}),
                             ("reference_hashes", {}), ("vendor_revision", "changed"), ("layout", {})):
            original = self.meta[field]
            self.meta[field] = value
            self.save()
            with self.subTest(field=field), self.assertRaises(inference.SetupError):
                self.check()
            self.meta[field] = original

    def test_embedded_model_input_logits_must_match_the_accepted_binary_not_just_headers(self):
        original = self.binary.read_bytes()
        for name in ("reference_model_pte", "reference_input", "reference_logits"):
            changed = bytearray(original)
            changed[self.symbols[name][0] - prepare.PROFILE.mram_base] ^= 1
            self.binary.write_bytes(changed)
            self.meta["bin_sha256"] = inference.sha256(self.binary)
            self.save()
            with self.subTest(name=name), self.assertRaisesRegex(inference.SetupError, "Embedded"):
                self.check()

    def test_broken_camera_vector_is_rejected_even_with_updated_binary_hash(self):
        data = bytearray(self.binary.read_bytes())
        data[(333 + 16) * 4] ^= 1
        self.binary.write_bytes(data)
        self.meta["bin_sha256"] = inference.sha256(self.binary)
        self.save()
        with self.assertRaises(inference.SetupError):
            self.check()
