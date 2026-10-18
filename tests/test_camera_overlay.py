import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import camera_overlay_firmware as overlay
from tools.inference import ROOT, SetupError


class OverlayBuildTests(unittest.TestCase):
    def test_preview_rotates_only_inactive_buffer_before_clean_and_flip(self):
        original = (ROOT / "firmware/camera_inference/src/preview.cpp").read_text()
        text = overlay.preview_variant(original)
        render = text.index("handgpt::draw_asl_panel(g_preview_lcd[next]")
        self.assertLess(text.index("handgpt::preview_rgb565("), render)
        rotation = text.index("handgpt::rotate_lcd180(g_preview_lcd[next]")
        self.assertLess(render, rotation)
        self.assertLess(rotation, text.index("clean(g_preview_lcd[next]"))
        self.assertEqual(text.count("handgpt::rotate_lcd180("), 1)
        self.assertNotIn("preview_rgb565_rotate180", text)
        self.assertLess(render, text.index("CDC200_FRAMEBUF_UPDATE_VSYNC"))
        self.assertIn("symbol, true)", text)
        self.assertIn("NOT camera recognition", text)
        # Neither camera DMA ownership nor display flip waiting was replaced.
        self.assertIn("if (!ready || display_error || !capture())", text)
        self.assertIn("static_cast<uint32_t>(scanlines - before) < 2u", text)
        with self.assertRaises(SetupError):
            overlay.preview_variant(text)  # Fail closed on duplicate injection.

    def test_main_preserves_selftest_and_capture_flow_with_explicit_ui_banner(self):
        original = (ROOT / "firmware/camera_inference/src/main.cpp").read_text()
        text = overlay.main_variant(original)
        self.assertIn("NOT LIVE ASL", text)
        self.assertIn("LCD rotated 180 degrees; v4 capture unchanged", text)
        self.assertIn("if (!run_stored_image())", text)
        self.assertIn("CAPTURE READY v=4", text)
        self.assertEqual(text.count("v=4"), original.count("v=4"))
        self.assertIn("camera_export_frame()", text)

    def test_private_generator_does_not_redirect_working_capture_builder(self):
        from tools import camera_inference_firmware as camera
        workspace, configure = camera.WORKSPACE, camera.configure
        private = overlay.private_generator()
        self.assertEqual(private.WORKSPACE, overlay.WORKSPACE)
        self.assertEqual(camera.WORKSPACE, workspace)
        self.assertIs(camera.configure, configure)
        self.assertIsNot(private, camera)

    def test_refuses_existing_workspace_and_parallel_build(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(overlay, "WORKSPACE", Path(directory)):
                with self.assertRaisesRegex(SetupError, "already exists"):
                    overlay.build(argparse.Namespace(jobs=1))
        with self.assertRaisesRegex(SetupError, "one build job"):
            overlay.build(argparse.Namespace(jobs=2))


@unittest.skipUnless(shutil.which("c++"), "Host C++ compiler required")
class OverlayNativeTests(unittest.TestCase):
    def test_generated_preview_preserves_dma_ownership_and_error_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("RTE_Components.h", "RTE_Device.h", "Driver_CPI.h", "Driver_ISP.h", "Driver_CDC200.h",
                         "vsi_comm_video.h", "isp_calibration.h"):
                (root / name).write_text('#include "fake_sdk.h"\n')
            source = root / "preview.cpp"
            source.write_text(overlay.preview_variant((ROOT / "firmware/camera_inference/src/preview.cpp").read_text()))
            harness = (ROOT / "tests/native/camera_preview_test.cpp").read_text()
            harness = '#include "asl_display.h"\n#include "preview_pixels.h"\n#include <algorithm>\n#include <vector>\n' + harness
            # Asymmetric source verifies both axes and catches double rotation.
            harness = harness.replace('std::memset(g_preview_rgb, 255, 480 * 480);',
                'std::memset(g_preview_rgb, 255, 480 * 480);\n'
                '    g_preview_rgb[0] = 0; g_preview_rgb[480 * 480] = 255;\n'
                '    g_preview_rgb[2 * 480 * 480 + 479] = 255;')
            old_checks = ('        assert(pixels[0] == 0xf800 && pixels[479 * 480 + 479] == 0xf800);\n'
                          '        assert(pixels[480 * 480] == 0 && pixels[800 * 480 - 1] == 0);')
            self.assertEqual(harness.count(old_checks), 1)
            harness = harness.replace(old_checks,
                '        assert(pixels[800 * 480 - 1] == 0x07e0); // Original camera top-left.\n'
                '        assert(pixels[320 * 480] == 0xf800); // Rotated camera begins here.')
            harness = harness.replace("void fake_clean(void*, size_t) { ++clean_calls; }", '''
void fake_clean(void* pointer, size_t bytes) {
    ++clean_calls;
    if (bytes == sizeof(g_preview_lcd[0])) {
        assert(!capturing && invalidations);
        const auto* pixels = static_cast<uint16_t*>(pointer);
        assert(pixels == g_preview_lcd[(flips + 1) % 2]);
        std::vector<uint16_t> expected(480 * 800, 0);
        assert(handgpt::preview_rgb565(g_preview_rgb, sizeof(g_preview_rgb), 480, 480,
                                      expected.data(), 480, 800));
        const auto value = static_cast<handgpt::AslLetter>((smoke_millis() / 1500u) % 6u);
        assert(handgpt::draw_asl_panel(expected.data(), expected.size(), value, true));
        std::reverse(expected.begin(), expected.end());
        assert(std::memcmp(pixels, expected.data(), expected.size() * sizeof(uint16_t)) == 0);
    }
}''')
            harness_path = root / "harness.cpp"
            harness_path.write_text(harness)
            executable = root / "preview"
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I", str(root), "-I", str(ROOT / "tests/native/camera_inference_mocks"),
                            "-I", str(ROOT / "firmware/camera_inference/include"),
                            "-I", str(ROOT / "firmware/smoke/include"), "-I", str(overlay.HEADER.parent),
                            str(source), str(harness_path), "-o", str(executable)], check=True, capture_output=True)
            for scenario in ("success", "partial-then-complete", "not-initialized", "init-error", "queue-error",
                             "capture-timeout", "fill-not-complete", "wrap-not-complete", "capture-stopped-not-complete",
                             "camera-error", "late-camera-error", "process-error", "gain-overflow", "stop-error",
                             "display-timeout", "display-error"):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(executable), scenario], check=True, capture_output=True, timeout=10)

    def test_generated_main_preserves_selftest_and_capture_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "image_config.h").write_text('#define IMAGE_MODEL_SHA256 "test-model"\n#define IMAGE_INPUT_SHA256 "test-input"\n')
            source = root / "main.cpp"
            source.write_text(overlay.main_variant((ROOT / "firmware/camera_inference/src/main.cpp").read_text()))
            compiler = ["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I", str(root), "-I", str(ROOT / "firmware/camera_inference/include"),
                        "-I", str(ROOT / "firmware/smoke/include")]
            subprocess.run(compiler + ["-Dmain=coexist_main", "-c", str(source), "-o", str(root / "main.o")],
                           check=True, capture_output=True)
            executable = root / "main_test"
            subprocess.run(compiler + [str(root / "main.o"), str(ROOT / "tests/native/camera_coexist_main_test.cpp"),
                           "-o", str(executable)], check=True, capture_output=True)
            for scenario in ("success", "board-error", "preview-init-error", "pre-frame-error", "npu-error", "image-error",
                             "no-display-events", "post-frame-error", "uart-error", "capture-error", "capture-timeout", "settle-error"):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(executable), scenario], check=True, capture_output=True, timeout=10)
                    if scenario == "success":
                        self.assertIn(b"NOT LIVE ASL", result.stdout)
                        self.assertIn(b"CAPTURE DONE v=4", result.stdout)

    def test_glyphs_decision_and_temporal_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "overlay"
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I", str(overlay.HEADER.parent),
                            "-I", str(ROOT / "firmware/camera_inference/include"),
                            str(ROOT / "tests/native/asl_display_test.cpp"),
                            "-o", str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True, timeout=10)
            result = subprocess.run([str(executable), "render"], check=True, capture_output=True, timeout=10)
            self.assertEqual(len(result.stdout), 6 * 480 * 800 * 2)
            frames = [result.stdout[i*480*800*2:(i+1)*480*800*2] for i in range(6)]
            self.assertEqual(len(set(frames)), 6)
            # After whole-screen rotation the camera occupies scanout rows 320..799.
            self.assertEqual(len({frame[480*320*2:] for frame in frames}), 1)


if __name__ == "__main__":
    unittest.main()
