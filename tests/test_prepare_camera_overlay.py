import contextlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools import inference
from tools import prepare_camera_overlay as overlay
from tools import prepare_camera_inference as capture
import test_prepare_camera_inference as camera_guards
import test_prepare_stored_image as package_guards


class OverlayPackageTests(camera_guards.PrepareCameraInferenceTests):
    """Re-run package CRC/map/TOC and staged-tool mutation tests with UI names."""
    def setUp(self):
        self.engine = overlay.package_engine()
        patch = mock.patch.object(camera_guards, "prepare", self.engine)
        patch.__enter__()
        self.addCleanup(patch.__exit__, None, None, None)
        super().setUp()

    def test_cli_has_no_program_command_or_write_flag(self):
        for args in (("program", "--write"), ("review", "--write"),
                     ("prepare", "--setools-root", "/unused", "--image", "/unused", "--hp")):
            result = subprocess.run([sys.executable, "-B", "-m", "tools.prepare_camera_overlay", *args],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)

    def test_capture_and_overlay_packages_are_not_interchangeable(self):
        text, package, device = package_guards.package_fixture()
        with self.assertRaises(inference.SetupError):
            capture.verify_package(text, package, 1984, device)
        with self.assertRaises(inference.SetupError):
            self.engine.verify_package(text.replace(overlay.IMAGE_NAME, capture.IMAGE_NAME), package, 1984, device)

    def test_capture_receipt_rejected_before_build_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "latest.json").write_text(json.dumps(dict(receipt="receipt.json")))
            (root / "receipt.json").write_text(json.dumps(dict(format=1, target=capture.TARGET)))
            with mock.patch.object(self.engine, "PROGRAM", root), mock.patch.object(self.engine, "check_build") as check:
                with self.assertRaises(inference.SetupError):
                    self.engine.load_prepared()
                check.assert_not_called()

    def test_smoke_build_cannot_be_used_as_stored_image_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps(dict(target="DevKit-E8-HP", build="debug")))
            with mock.patch.object(overlay, "FIRMWARE", root), mock.patch.object(overlay.accepted, "check_build") as check:
                with self.assertRaises(inference.SetupError):
                    overlay.check_build(Path("unused"))
                check.assert_not_called()

    def test_engine_does_not_modify_capture_package_globals(self):
        before = (capture.PROGRAM, capture.IMAGE_NAME, capture.TARGET, capture.check_build)
        engine = overlay.package_engine()
        self.assertEqual(engine.PROGRAM, overlay.PROGRAM)
        self.assertNotEqual(engine.PROGRAM, capture.PROGRAM)
        self.assertNotEqual(engine.TARGET, capture.TARGET)
        self.assertIs(engine.check_build, overlay.check_build)
        self.assertEqual(before, (capture.PROGRAM, capture.IMAGE_NAME, capture.TARGET, capture.check_build))

    def test_private_staging_cannot_be_used_as_installed_toolkit(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "camera-inference-fast-programming/old/setools"
            root.mkdir(parents=True)
            with mock.patch.object(overlay.common, "WORK", work), mock.patch.object(overlay, "package_engine") as engine:
                with self.assertRaisesRegex(inference.SetupError, "installed SETOOLS"):
                    overlay.prepare(root, Path("cat.jpg"), True)
                engine.assert_not_called()


class OverlayBuildValidationTests(camera_guards.CameraBuildValidationTests):
    """Re-use binary/layout mutation coverage plus overlay-specific provenance."""
    def setUp(self):
        real_root = inference.ROOT
        texts = {n: (real_root / "firmware/camera_inference/src" / n).read_text()
                 for n in ("main.cpp", "preview.cpp")}
        platform = (real_root / "firmware/smoke/src/platform.cpp").read_text()
        camera_helper = Path(overlay.camera.__file__).read_bytes()
        overlay_helper = Path(overlay.overlay.__file__).read_bytes()
        header = overlay.overlay.HEADER.read_bytes()
        rotation_header = overlay.overlay.ROTATION_HEADER.read_bytes()
        super().setUp()
        # The parent fixture supplies synthetic ELF/SDK/layout and prerequisite.
        for obj, name, value in ((overlay, "ROOT", self.root), (overlay, "FIRMWARE", self.work),
                                 (overlay.camera, "APPLICATION", self.root / "firmware/camera_inference"),
                                 (overlay.overlay, "HEADER", self.root / "firmware/camera_overlay/include/asl_display.h"),
                                 (overlay.overlay, "ROTATION_HEADER", self.root / "firmware/camera_overlay/include/camera_rotation.h"),
                                 (overlay.overlay, "__file__", str(self.root / "tools/camera_overlay_firmware.py")),
                                 (overlay.camera, "__file__", str(self.root / "tools/camera_inference_firmware.py"))):
            self.patches.enter_context(mock.patch.object(obj, name, value))
        self.write(Path(overlay.camera.__file__), camera_helper)
        self.write(Path(overlay.overlay.__file__), overlay_helper)
        self.write(overlay.overlay.HEADER, header)
        self.write(overlay.overlay.ROTATION_HEADER, rotation_header)
        for name, convert in (("main.cpp", overlay.overlay.main_variant), ("preview.cpp", overlay.overlay.preview_variant)):
            self.write(overlay.camera.APPLICATION / "src" / name, texts[name].encode())
            self.write(self.work / "generated" / name, convert(texts[name]).encode())
        self.write(self.root / "firmware/smoke/src/platform.cpp", platform.encode())
        self.write(self.work / "generated/platform.cpp", overlay.camera.platform_port(platform).encode())
        self.meta.update(variant="test-ui-only", overlay=overlay.OVERLAY)
        for p in (overlay.overlay.HEADER, overlay.overlay.ROTATION_HEADER, Path(overlay.overlay.__file__),
                  self.work / "generated/main.cpp", self.work / "generated/preview.cpp"):
            self.meta["source_sha256"][str(p.relative_to(self.root))] = inference.sha256(p)
        self.meta["source_sha256"] = {n: inference.sha256(self.root/n) for n in self.meta["source_sha256"]}
        inputs = [Path(overlay.overlay.__file__), Path(overlay.camera.__file__), overlay.overlay.HEADER,
                  overlay.overlay.ROTATION_HEADER]
        inputs += list(overlay.camera.APPLICATION.rglob("*.cpp")) + list(overlay.camera.APPLICATION.rglob("*.h"))
        self.meta["overlay_input_sha256"] = {str(p.relative_to(self.root)): inference.sha256(p) for p in inputs}
        self.meta["generated_sha256"] = {str(p.relative_to(self.work)): inference.sha256(p) for folder in ("assets", "RTE", "generated")
                                         for p in (self.work/folder).rglob("*") if p.is_file()}
        commands = [dict(file=str(self.work/"generated"/n), directory=str(self.work))
                    for n in ("main.cpp", "preview.cpp", "platform.cpp")]
        commands.append(dict(file=str(overlay.camera.APPLICATION / "src/frame_export.cpp"), directory=str(self.work)))
        self.write(self.commands, json.dumps(commands).encode())
        self.meta["compile_commands_sha256"] = inference.sha256(self.commands)
        self.save()

    def check(self):
        return overlay.check_build(self.root / "cat.jpg")

    def test_overlay_variant_and_live_claims_fail(self):
        for name, value in (("variant", "capture"), ("overlay", {}),
                             ("overlay", {**overlay.OVERLAY, "live_asl": True}),
                             ("overlay", {**overlay.OVERLAY, "screen_rotation_degrees": 0}),
                             ("capture", {**overlay.camera.CAPTURE, "protocol": 5})):
            old = self.meta[name]
            self.meta[name] = value
            self.save()
            with self.subTest(name=name), self.assertRaises(inference.SetupError):
                self.check()
            self.meta[name] = old

    def test_missing_overlay_identities_fail(self):
        self.meta["overlay_input_sha256"] = {}
        self.save()
        with self.assertRaises(inference.SetupError):
            self.check()

    def test_updated_hash_cannot_hide_changed_generated_render_or_port(self):
        for name in ("main.cpp", "preview.cpp", "platform.cpp"):
            path = self.work / "generated" / name
            old = path.read_bytes()
            path.write_bytes(old + b"\n// changed after generation\n")
            self.meta["source_sha256"][str(path.relative_to(self.root))] = inference.sha256(path)
            self.meta["generated_sha256"][str(path.relative_to(self.work))] = inference.sha256(path)
            self.save()
            with self.subTest(name=name), self.assertRaisesRegex(inference.SetupError, "transformation"):
                self.check()
            path.write_bytes(old)
            self.meta["source_sha256"][str(path.relative_to(self.root))] = inference.sha256(path)
            self.meta["generated_sha256"][str(path.relative_to(self.work))] = inference.sha256(path)

    def test_compiling_capture_source_instead_of_overlay_is_rejected(self):
        for name in ("main.cpp", "preview.cpp"):
            with self.subTest(name=name):
                commands = json.loads(self.commands.read_text())
                commands[0]["file"] = str(overlay.camera.APPLICATION / "src" / name)
                self.commands.write_text(json.dumps(commands))
                self.meta["compile_commands_sha256"] = inference.sha256(self.commands)
                self.save()
                with self.assertRaisesRegex(inference.SetupError, "exactly one generated"):
                    self.check()

    def test_rotated_exporter_cannot_replace_the_upright_v4_capture(self):
        commands = json.loads(self.commands.read_text())
        commands[-1]["file"] = str(self.work / "generated/frame_export.cpp")
        self.commands.write_text(json.dumps(commands))
        self.meta["compile_commands_sha256"] = inference.sha256(self.commands)
        self.save()
        with self.assertRaisesRegex(inference.SetupError, "unchanged v4 frame exporter"):
            self.check()
