import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from tools import asl_live_firmware as live
from tools.inference import ROOT, SetupError
from tools import asl_selftest
from tools.asl_contract import LEGACY_CLASSES, class_header


class LiveBuildTests(unittest.TestCase):
    def test_source_observes_one_complete_rgb_frame_and_never_cycles(self):
        original = (ROOT / "firmware/camera_inference/src/preview.cpp").read_text()
        text = live.preview_variant(original)
        inference = text.index("live_asl_predict(g_preview_rgb, sizeof(g_preview_rgb))")
        self.assertLess(text.index("if (!ready || display_error || !capture())"), inference)
        self.assertLess(inference, text.index("handgpt::preview_rgb565("))
        self.assertLess(text.index("draw_live_asl_panel"), text.index("rotate_lcd180"))
        self.assertLess(text.index("rotate_lcd180"), text.index("clean(g_preview_lcd[next]"))
        self.assertNotIn("% 6", text)
        self.assertNotIn("1500", text)
        with self.assertRaises(SetupError):
            live.preview_variant(text)

    def test_acknowledgement_jobs_and_existing_workspace_fail_closed(self):
        args = argparse.Namespace(experimental=False, jobs=1, layout=live.PROFILE.name)
        with self.assertRaises(SetupError):
            live.build(args)
        args.experimental, args.jobs = True, 2
        with self.assertRaises(SetupError):
            live.build(args)
        args.jobs = 1
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(live, "WORKSPACE", Path(directory)):
            with self.assertRaisesRegex(SetupError, "workspace exists"):
                live.build(args)

    def test_generated_gate_header_rejects_missing_invalid_and_weakened_policy(self):
        gate = asl_selftest.policy(0.18568913638591766)
        header = live.gate_header(gate)
        self.assertIn("kReferenceGateRevision = 2", header)
        self.assertIn("kReferenceOutputScale = 0.18568913638591766", header)
        for bad in ({}, {**gate, "output_scale": 0}, {**gate, "output_scale": float('nan')},
                    {**gate, "max_output_steps": 9}, {**gate, "matching_display_decision": False}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                live.gate_header(bad)


@unittest.skipUnless(shutil.which("c++"), "Host compiler required")
class LiveNativeTests(unittest.TestCase):
    def includes(self):
        return [arg for folder in ("firmware/asl_live/include", "firmware/camera_overlay/include",
                                  "firmware/camera_inference/include", "firmware/smoke/include")
                for arg in ("-I", str(ROOT / folder))]

    def test_selftest_limits_clean_panel_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "checks"
            (Path(directory) / 'asl_classes.h').write_text(class_header(LEGACY_CLASSES))
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", '-I', directory, *self.includes(),
                            str(ROOT / "tests/native/asl_live_checks_test.cpp"), "-o", str(exe)], check=True, capture_output=True)
            subprocess.run([str(exe)], check=True, capture_output=True, timeout=10)
            # Same vectors through the actual C++ gate and the observer's Python
            # gate. Binary float32 input prevents decimal fixture drift.
            reference = [3., -120., -90., -130., -40., -170., -40.]
            cases = [(reference, reference, 0.125, True)]
            for steps in (3, 8, 9):
                actual = reference.copy()
                actual[2] += steps * 0.125
                cases.append((actual, reference, 0.125, steps <= 8))
            actual = reference.copy()
            actual[2] = -74. # Cosine failure while within a deliberately larger scale bound.
            cases.append((actual, reference, 2., False))
            for scale in (0., -0.1, float('nan'), float('inf')):
                cases.append((reference, reference, scale, False))
            cases.append(([1.5, 0., -40., -40., -40., -40., -40.],
                          [1.375, 0., -40., -40., -40., -40., -40.], 0.125, False))
            cases.append(([2.875, 3., -90., -130., -40., -170., -40.],
                          [3., 2.875, -90., -130., -40., -170., -40.], 0.125, False))
            old_reference = [2.971026, -12.812551, -9.284457, -13.740996, -4.456539, -17.083401, -4.085161]
            old_actual = [2.971026, -12.441173, -8.727389, -13.740996, -4.827918, -17.083401, -4.085161]
            cases.append((old_actual, old_reference, 0.18568913638591766, True))
            cases.append(([float('nan'), *reference[1:]], reference, 0.125, False))
            for actual, expected, scale, passed in cases:
                payload = struct.pack('<15f', *actual, *expected, scale)
                values = struct.unpack('<15f', payload)
                result = subprocess.run([str(exe), '--compare'], input=payload, capture_output=True, check=True)
                self.assertEqual(result.stdout.strip(), b'1' if passed else b'0')
                if scale > 0 and scale != float('inf'):
                    self.assertEqual(asl_selftest.compare(values[:7], values[7:14], asl_selftest.policy(scale)), passed)

    def test_main_requires_selftest_and_display_events_before_live(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "image_config.h").write_text('#define IMAGE_MODEL_SHA256 "model"\n#define IMAGE_INPUT_SHA256 "input"\n')
            compiler = ["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(root), *self.includes()]
            subprocess.run([*compiler, "-Dmain=live_main", "-c", str(live.APPLICATION / "src/main.cpp"),
                            "-o", str(root / "main.o")], check=True, capture_output=True)
            exe = root / "main"
            subprocess.run([*compiler, str(root / "main.o"), str(ROOT / "tests/native/asl_live_main_test.cpp"),
                            "-o", str(exe)], check=True, capture_output=True)
            for scenario in ("success", "board-error", "init-error", "pre-frame-error", "npu-error",
                             "selftest-error", "selftest-325", "display-stalled", "post-frame-error", "prediction-fault"):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(exe), scenario], check=True, capture_output=True, timeout=10)
                    self.assertEqual(b"LIVE ASL START" in result.stdout,
                                     scenario in ("success", "post-frame-error", "prediction-fault"))
                    self.assertNotIn(b"CAPTURE READY", result.stdout)
                    if scenario == "prediction-fault":
                        self.assertIn(b"display=--", result.stdout)
                        self.assertIn(b"fault=1", result.stdout)

    def test_generated_preview_preserves_ownership_and_real_result_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("RTE_Components.h", "RTE_Device.h", "Driver_CPI.h", "Driver_ISP.h", "Driver_CDC200.h",
                         "vsi_comm_video.h", "isp_calibration.h"):
                (root / name).write_text('#include "fake_sdk.h"\n')
            source = root / "preview.cpp"
            source.write_text(live.preview_variant((ROOT / "firmware/camera_inference/src/preview.cpp").read_text()))
            harness = (ROOT / "tests/native/camera_preview_test.cpp").read_text()
            harness = '#include "live_asl.h"\n#include "live_display.h"\n#include "preview_pixels.h"\n#include <algorithm>\n#include <vector>\n' + harness
            harness = harness.replace("static std::string scenario;", "static std::string scenario;\nstatic unsigned observations;\nstatic bool blanking, selftest_panel;")
            old_checks = ('        assert(pixels[0] == 0xf800 && pixels[479 * 480 + 479] == 0xf800);\n'
                          '        assert(pixels[480 * 480] == 0 && pixels[800 * 480 - 1] == 0);')
            self.assertEqual(harness.count(old_checks), 1)
            harness = harness.replace(old_checks, "        assert(pixels[800 * 480 - 1] == (blanking ? 0 : 0xf800));")
            harness = harness.replace("void fake_clean(void*, size_t) { ++clean_calls; }", '''
void fake_clean(void* pointer, size_t bytes) {
    ++clean_calls;
    if (bytes == sizeof(g_preview_lcd[0])) {
        assert(!capturing && invalidations);
        assert(pointer == g_preview_lcd[(flips + 1) % 2]);
        std::vector<uint16_t> expected(480 * 800);
        if (!blanking) { assert(handgpt::preview_rgb565(g_preview_rgb, sizeof(g_preview_rgb), 480, 480, expected.data(), 480, 800)); }
        const auto letter = blanking ? handgpt::AslLetter::None : static_cast<handgpt::AslLetter>(observations % 6);
        if (selftest_panel) { assert(handgpt::draw_selftest_failure(expected.data(), expected.size(), 325)); }
        else { assert(handgpt::draw_live_asl_panel(expected.data(), expected.size(), letter)); }
        std::reverse(expected.begin(), expected.end());
        assert(std::memcmp(pointer, expected.data(), expected.size() * sizeof(uint16_t)) == 0);
    }
}''')
            harness = harness.replace("    assert(dequeues == 0);", '''
    if (scenario == "success" || scenario == "partial-then-complete") {
        const auto previous = observations, previous_queues = queues;
        blanking = true;
        assert(live_asl_blank_display());
        selftest_panel = true;
        assert(live_asl_selftest_failure(325));
        assert(observations == previous && queues == previous_queues);
    }
    if (scenario == "display-timeout" || scenario == "display-error") {
        assert(!live_asl_blank_display()); // Ownership is unknown; never write a possibly scanning buffer.
        assert(!live_asl_selftest_failure(325));
    }
    assert(dequeues == 0);''')
            harness += '''
void live_asl_predict(const uint8_t* rgb, size_t bytes) {
    assert(rgb == g_preview_rgb && bytes == sizeof(g_preview_rgb));
    assert(!capturing && completed && processed && invalidations && !buffer_pending);
    ++observations;
}
handgpt::AslLetter live_asl_letter(uint32_t) { return static_cast<handgpt::AslLetter>(observations % 6); }
'''
            (root / "harness.cpp").write_text(harness)
            exe = root / "preview"
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(root),
                            "-I", str(ROOT / "tests/native/camera_inference_mocks"), *self.includes(),
                            str(source), str(root / "harness.cpp"), "-o", str(exe)], check=True, capture_output=True)
            for scenario in ("success", "partial-then-complete", "not-initialized", "init-error", "queue-error",
                             "capture-timeout", "fill-not-complete", "wrap-not-complete", "capture-stopped-not-complete",
                             "camera-error", "late-camera-error", "process-error", "gain-overflow", "stop-error",
                             "display-timeout", "display-error"):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(exe), scenario], check=True, capture_output=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
