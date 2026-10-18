import argparse
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tools import camera_inference_firmware as camera
from tools import inference
import test_stored_image_firmware as stored_tests


class CameraInferenceFirmwareTests(unittest.TestCase):
    def fixture(self):
        symbols, binary = stored_tests.StoredImageFirmwareTests().fixture()
        symbols["g_preview_rgb"] = (camera.SRAM1_BASE, camera.CAMERA_BYTES, "B")
        symbols["g_preview_lcd"] = (camera.SRAM1_BASE + camera.CAMERA_BYTES, camera.LCD_BYTES, "B")
        symbols["handgpt::kResizeKernels"] = (camera.image.PROFILE.mram_base + 0x2800, 128 * 36, "V")
        for index, (irq, name) in enumerate(camera.PERIPHERAL_IRQS.items()):
            address = camera.image.PROFILE.mram_base + 0x2000 + index * 32
            symbols[name] = (address, 32, "T")
            struct.pack_into("<I", binary, (irq + 16) * 4, address | 1)
        return symbols, binary

    def test_valid_coexistence_keeps_inference_in_sram0_and_video_in_sram1(self):
        symbols, binary = self.fixture()
        report = camera.verify_layout(symbols, binary, 3835520)
        self.assertEqual(report["sram1_reserved_bytes"], 2227200)
        self.assertEqual(report["sram1_headroom"], 1967104)
        self.assertTrue(report["camera_display_vectors_verified"])

    def test_camera_cannot_overlap_the_verified_npu_bank(self):
        symbols, binary = self.fixture()
        symbols["g_preview_rgb"] = (0x02000000, camera.CAMERA_BYTES, "B")
        with self.assertRaises(inference.SetupError): camera.verify_layout(symbols, binary, 3835520)

    def test_resize_coefficients_cannot_be_missing_in_ram_or_wrong_size(self):
        for value in (None, (0x02000000, 4608, "V"), (0x80003800, 4604, "V"), (0x80003800, 4608, "D")):
            symbols, binary = self.fixture()
            if value is None:
                del symbols["handgpt::kResizeKernels"]
            else:
                symbols["handgpt::kResizeKernels"] = value
            with self.subTest(value=value), self.assertRaisesRegex(inference.SetupError, "read-only in MRAM"):
                camera.verify_layout(symbols, binary, 3835520)

    def test_overlapping_or_oversized_video_buffers_fail(self):
        for address, size in ((camera.SRAM1_BASE + 32, camera.LCD_BYTES), (camera.SRAM1_END - 32, camera.LCD_BYTES),
                              (camera.SRAM1_BASE + camera.CAMERA_BYTES, camera.LCD_BYTES + 32)):
            symbols, binary = self.fixture()
            symbols["g_preview_lcd"] = (address, size, "B")
            with self.subTest(address=address, size=size), self.assertRaises(inference.SetupError):
                camera.verify_layout(symbols, binary, 3835520)

    def test_weak_missing_or_wrong_peripheral_vectors_fail(self):
        for irq, name in camera.PERIPHERAL_IRQS.items():
            symbols, binary = self.fixture()
            a, z, _ = symbols[name]
            symbols[name] = (a, z, "W")
            with self.subTest(name=name), self.assertRaises(inference.SetupError):
                camera.verify_layout(symbols, binary, 3835520)
            symbols[name] = (a, z, "T")
            struct.pack_into("<I", binary, (irq + 16) * 4, a + 3)
            with self.assertRaises(inference.SetupError): camera.verify_layout(symbols, binary, 3835520)

    def test_startup_must_not_touch_sram1_before_power_request(self):
        # Preserve the existing SRAM0 zero entry and extend the table instead.
        symbols, binary = self.fixture()
        struct.pack_into("<II", binary, 0x810, camera.SRAM1_BASE, camera.CAMERA_BYTES // 4)
        address, size, kind = symbols["__zero_table_end__"]
        symbols["__zero_table_end__"] = (address + 8, size, kind)
        with self.assertRaisesRegex(inference.SetupError, "must not touch SRAM1"):
            camera.verify_layout(symbols, binary, 3835520)

    def test_graphics_heap_or_competing_uart_tick_port_is_rejected(self):
        for name in ("d0_heap", "tracelib_init", "clk_init", "d2_init"):
            symbols, binary = self.fixture()
            symbols[name] = (0x02000000, 16, "B")
            with self.subTest(name=name), self.assertRaises(inference.SetupError):
                camera.verify_layout(symbols, binary, 3835520)

    def test_power_hook_is_private_and_runs_before_uart_and_tick_initialization(self):
        text = (inference.ROOT / "firmware/smoke/src/platform.cpp").read_text()
        result = camera.platform_port(text)
        self.assertLess(result.index("preview_power_init"), result.index("SysTick_Config"))
        self.assertLess(result.index("preview_power_init"), result.index("uart->Initialize"))
        with self.assertRaises(inference.SetupError): camera.platform_port(text + "\n    se_services_port_init();")

    def test_layout_is_explicit_and_cannot_use_the_current_smoke_profile(self):
        with self.assertRaises(inference.SetupError):
            camera.build(argparse.Namespace(layout="current-E8-HP"))

    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler unavailable")
    def test_native_pixel_conversion_dimensions_padding_and_timeout_wrap(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pixels"
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(camera.APPLICATION / "include"),
                            str(inference.ROOT / "tests/native/camera_preview_pixels_test.cpp"), "-o", str(output)], check=True, capture_output=True, text=True)
            subprocess.run([str(output)], check=True, timeout=10)

    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler unavailable")
    def test_native_camera_display_ownership_errors_and_bounded_waits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("RTE_Components.h", "RTE_Device.h", "Driver_CPI.h", "Driver_ISP.h", "Driver_CDC200.h", "vsi_comm_video.h", "isp_calibration.h"):
                (root / name).write_text('#include "fake_sdk.h"\n')
            output = root / "preview"
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I", str(root), "-I", str(inference.ROOT / "tests/native/camera_inference_mocks"),
                            "-I", str(camera.APPLICATION / "include"), "-I", str(inference.ROOT / "firmware/smoke/include"),
                            str(camera.APPLICATION / "src/preview.cpp"), str(inference.ROOT / "tests/native/camera_preview_test.cpp"),
                            "-o", str(output)], check=True, capture_output=True, text=True)
            for scenario in ("success", "partial-then-complete", "not-initialized", "init-error", "queue-error", "capture-timeout",
                             "fill-not-complete", "wrap-not-complete", "capture-stopped-not-complete", "camera-error",
                             "late-camera-error", "process-error", "gain-overflow",
                             "stop-error", "display-timeout", "display-error"):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(output), scenario], check=True, capture_output=True, text=True, timeout=10)

    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler unavailable")
    def test_native_coexistence_acceptance_requires_preview_before_and_after_selftest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "image_config.h").write_text('#define IMAGE_MODEL_SHA256 "test-model"\n#define IMAGE_INPUT_SHA256 "test-input"\n')
            includes = ["-I", str(root), "-I", str(camera.APPLICATION / "include"), "-I", str(inference.ROOT / "firmware/smoke/include")]
            compiler = ["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror"]
            subprocess.run(compiler + includes + ["-Dmain=coexist_main", "-c", str(camera.APPLICATION / "src/main.cpp"),
                            "-o", str(root / "main.o")], check=True, capture_output=True, text=True)
            output = root / "main_test"
            subprocess.run(compiler + includes + [str(root / "main.o"), str(inference.ROOT / "tests/native/camera_coexist_main_test.cpp"),
                            "-o", str(output)], check=True, capture_output=True, text=True)
            for scenario in ("success", "board-error", "preview-init-error", "pre-frame-error", "npu-error", "image-error",
                              "no-display-events", "post-frame-error", "uart-error", "capture-error", "capture-timeout", "settle-error"):
                with self.subTest(scenario=scenario):
                    run = subprocess.run([str(output), scenario], check=True, capture_output=True, text=True, timeout=10)
                    self.assertEqual("COEXIST SELFTEST PASS" in run.stdout,
                                     scenario in ("success", "capture-error", "capture-timeout", "settle-error"))
                    self.assertEqual("CAPTURE DONE v=4" in run.stdout, scenario == "success")
                    self.assertEqual("CAPTURE EXPORT LOST" in run.stdout,
                                     scenario in ("capture-error", "capture-timeout"))

    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler unavailable")
    def test_native_run_profile_preserves_resources_and_powers_both_sram_banks_and_hp_npu(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("services_lib_api.h", "services_lib_bare_metal.h"):
                (root / name).write_text('#include "fake_power_sdk.h"\n')
            output = root / "power_test"
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I", str(root), "-I", str(inference.ROOT / "tests/native/camera_inference_mocks"),
                            "-I", str(camera.APPLICATION / "include"), str(camera.APPLICATION / "src/power.cpp"),
                            str(inference.ROOT / "tests/native/camera_power_test.cpp"), "-o", str(output)],
                           check=True, capture_output=True, text=True)
            for scenario in ("success", "get-service-error", "get-transport-error", "set-service-error", "set-transport-error"):
                with self.subTest(scenario=scenario):
                    subprocess.run([str(output), scenario], check=True, capture_output=True, text=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
