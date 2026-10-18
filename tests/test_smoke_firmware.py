import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tools import inference
from tools import smoke_firmware as smoke


class SmokeFirmwareTests(unittest.TestCase):
    def layout(self):
        symbols = {
            "__Vectors": (0x80200000, 1984, "R"),
            "IRQ366_Handler": (0x80201000, 32, "T"),
            "SysTick_Handler": (0x80201100, 16, "T"),
            "HardFault_Handler": (0x80201200, 16, "T"),
            "_write": (0x80201300, 32, "T"),
            "et_pal_current_ticks": (0x80201400, 32, "T"),
            "g_smoke_status": (0x20000000, 60, "D"),
            "g_smoke_npu_arena": (0x02000000, 4096, "B"),
            "g_smoke_npu_temporary_pool": (0x02001000, 32 * 1024, "B"),
            "reference_model_pte": (0x80202000, 2976, "r"),
            "__end__": (0x20030000, 0, "B"),
            "__HeapLimit": (0x20050000, 0, "B"),
            "__StackLimit": (0x200F8000, 0, "B"),
            "__StackTop": (0x20100000, 0, "B"),
        }
        binary = bytearray(4096)
        struct.pack_into("<I", binary, 15 * 4, symbols["SysTick_Handler"][0] | 1)
        struct.pack_into("<I", binary, (16 + 366) * 4, symbols["IRQ366_Handler"][0] | 1)
        return symbols, binary

    def test_layout_checks_real_vector_entries(self):
        symbols, binary = self.layout()
        result = smoke.verify_layout(symbols, binary, 2976)
        self.assertTrue(result["vector_table_verified"])
        self.assertEqual(result["heap_bytes"], 128 * 1024)
        self.assertEqual(result["stack_bytes"], 32 * 1024)
        self.assertEqual(result["temporary_pool_address"], "0x2001000")
        self.assertEqual(result["temporary_pool_bytes"], 32 * 1024)

    def test_npu_arena_in_tcm_is_rejected(self):
        symbols, binary = self.layout()
        symbols["g_smoke_npu_arena"] = (0x20001000, 4096, "B")
        with self.assertRaises(inference.SetupError):
            smoke.verify_layout(symbols, binary, 2976)

    def test_npu_temporary_pool_in_tcm_is_rejected(self):
        symbols, binary = self.layout()
        # Regression: this was the actual location of the old CPU-only temp pool.
        symbols["g_smoke_npu_temporary_pool"] = (0x200070E0, 32 * 1024, "B")
        with self.assertRaisesRegex(inference.SetupError, "temporary/scratch"):
            smoke.verify_layout(symbols, binary, 2976)

    def test_npu_temporary_pool_must_be_present_aligned_and_fully_in_sram(self):
        for pool in (None, (0x02001001, 32 * 1024, "B"),
                     (0x023FF000, 32 * 1024, "B"), (0x02001000, 0, "B"),
                     (0x02001000, 16 * 1024, "B")):
            symbols, binary = self.layout()
            if pool is None:
                del symbols["g_smoke_npu_temporary_pool"]
            else:
                symbols["g_smoke_npu_temporary_pool"] = pool
            with self.subTest(pool=pool):
                with self.assertRaises(inference.SetupError):
                    smoke.verify_layout(symbols, binary, 2976)

    def test_npu_pools_cannot_overlap(self):
        symbols, binary = self.layout()
        symbols["g_smoke_npu_temporary_pool"] = (0x02000020, 32 * 1024, "B")
        with self.assertRaisesRegex(inference.SetupError, "overlaps"):
            smoke.verify_layout(symbols, binary, 2976)

    def test_misaligned_or_wrong_size_model_is_rejected(self):
        for model in ((0x80202001, 2976, "r"), (0x20002000, 2976, "r"), (0x80202000, 2975, "r")):
            symbols, binary = self.layout()
            symbols["reference_model_pte"] = model
            with self.assertRaises(inference.SetupError):
                smoke.verify_layout(symbols, binary, 2976)

    def test_weak_npu_handler_is_rejected(self):
        symbols, binary = self.layout()
        symbols["IRQ366_Handler"] = (0x80201000, 32, "W")
        with self.assertRaises(inference.SetupError):
            smoke.verify_layout(symbols, binary, 2976)

    def test_default_handler_in_vector_table_is_rejected(self):
        symbols, binary = self.layout()
        struct.pack_into("<I", binary, (16 + 366) * 4, 0x80209999)
        with self.assertRaises(inference.SetupError):
            smoke.verify_layout(symbols, binary, 2976)

    def test_overlapping_heap_and_stack_is_rejected(self):
        symbols, binary = self.layout()
        symbols["__HeapLimit"] = (0x200FF000, 0, "B")
        with self.assertRaises(inference.SetupError):
            smoke.verify_layout(symbols, binary, 2976)

    def test_nm_parser_handles_sized_unsized_and_cpp_symbols(self):
        symbols = smoke.parse_symbols("20030000 B __end__\n80202000 00000ba0 r reference_model_pte\n80201000 00000020 T a::function(int)\n")
        self.assertEqual(symbols["__end__"][0], 0x20030000)
        self.assertEqual(symbols["reference_model_pte"][1], 2976)
        self.assertIn("a::function(int)", symbols)

    def test_arm_abi_matches_pinned_runtime(self):
        smoke.verify_arm_abi("Tag_ABI_enum_size: small\nTag_ABI_VFP_args: VFP registers\n")
        with self.assertRaises(inference.SetupError):
            smoke.verify_arm_abi("Tag_ABI_enum_size: int\nTag_ABI_VFP_args: VFP registers\n")
        with self.assertRaises(inference.SetupError):
            smoke.verify_arm_abi("Tag_ABI_enum_size: small\n")

    def test_linker_patch_is_guarded_and_keeps_npu_arena_before_dtcm_catchall(self):
        original = """  .code.at_itcm : {
    *(.text*)
  }
  .bss.at_sram0 (NOLOAD) : ALIGN(8)
  {
  }
  .bss : { *(.bss.*) }
  .readonly.at_mram : {
    *(.rodata*)
  }
"""
        result = smoke.smoke_linker(original)
        self.assertIn("*(.text.handgpt_itcm*)", result)
        self.assertIn("*(.text*)\n    *(.rodata*)", result)
        self.assertLess(result.index("*(.bss.handgpt_npu)"), result.index("*(.bss.*)"))
        with self.assertRaises(inference.SetupError):
            smoke.smoke_linker(original + "\n    *(.text*)")

    def artifacts(self, root):
        runtime, model = root / "runtime", root / "model"
        (runtime / "lib").mkdir(parents=True)
        model.mkdir()
        runtime_meta = {"executorch_revision": inference.REVISION,
                        "cpu": "cortex-m55", "npu": inference.TARGET, "libraries": {}}
        for name in smoke.WHOLE_LIBRARIES + smoke.SUPPORT_LIBRARIES:
            filename = f"lib{name}.a"
            data = name.encode()
            (runtime / "lib" / filename).write_bytes(data)
            runtime_meta["libraries"][filename] = hashlib.sha256(data).hexdigest()
        metadata = {"methods": [{"name": "forward",
            "inputs": [{"shape": [1, 16], "dtype": "FLOAT", "dim_order": [0, 1]}],
            "outputs": [{"shape": [1, 16], "dtype": "FLOAT", "dim_order": [0, 1]}],
            "delegates": ["EthosUBackend"], "planned_buffer_bytes": [96]}]}
        (model / "program_metadata.json").write_text(json.dumps(metadata))
        (model / "smoke_ethosu85_256.pte").write_bytes(b"reference")
        model_meta = {"executorch_revision": inference.REVISION, "model": "smoke",
                      "target": inference.TARGET, "memory_mode": "Sram_Only", "pte_bytes": 9,
                      "pte_sha256": inference.sha256(model / "smoke_ethosu85_256.pte"),
                      "metadata_sha256": inference.sha256(model / "program_metadata.json")}
        (runtime / "manifest.json").write_text(json.dumps(runtime_meta))
        (model / "manifest.json").write_text(json.dumps(model_meta))
        return runtime, model

    def test_matching_artifact_manifests_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, model = self.artifacts(Path(directory))
            smoke.check_artifacts(runtime, model)

    def test_modified_archive_is_rejected_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, model = self.artifacts(Path(directory))
            (runtime / "lib" / "libethosu_core_driver.a").write_bytes(b"different")
            with self.assertRaises(inference.SetupError):
                smoke.check_artifacts(runtime, model)

    def test_missing_library_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, model = self.artifacts(Path(directory))
            manifest = json.loads((runtime / "manifest.json").read_text())
            del manifest["libraries"]["libethosu_core_driver.a"]
            (runtime / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(inference.SetupError):
                smoke.check_artifacts(runtime, model)

    def test_wrong_memory_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, model = self.artifacts(Path(directory))
            manifest = json.loads((model / "manifest.json").read_text())
            manifest["memory_mode"] = "Dedicated_Sram"
            (model / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(inference.SetupError):
                smoke.check_artifacts(runtime, model)

    def test_mixed_executorch_revisions_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, model = self.artifacts(Path(directory))
            manifest = json.loads((model / "manifest.json").read_text())
            manifest["executorch_revision"] = "different"
            (model / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(inference.SetupError):
                smoke.check_artifacts(runtime, model)

    def test_planned_memory_budget_includes_per_buffer_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime, model = self.artifacts(Path(directory))
            metadata = json.loads((model / "program_metadata.json").read_text())
            metadata["methods"][0]["planned_buffer_bytes"] = [4095, 1]
            (model / "program_metadata.json").write_text(json.dumps(metadata))
            manifest = json.loads((model / "manifest.json").read_text())
            manifest["metadata_sha256"] = inference.sha256(model / "program_metadata.json")
            (model / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(inference.SetupError):
                smoke.check_artifacts(runtime, model)

    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler unavailable")
    def test_native_uart_completion_and_timeout_regressions(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "uart_check"
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-fno-exceptions", "-fno-rtti",
                            "-I", str(smoke.APPLICATION / "include"),
                            str(inference.ROOT / "tests/native/smoke_uart_test.cpp"),
                            "-o", str(executable)], check=True, capture_output=True, text=True)
            subprocess.run([str(executable)], check=True, timeout=10)

    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler unavailable")
    def test_main_reports_initialization_and_inference_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "main_check"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-I", str(smoke.APPLICATION / "include"),
                            str(smoke.APPLICATION / "src/main.cpp"),
                            str(inference.ROOT / "tests/native/smoke_main_stubs.cpp"),
                            "-o", str(executable)], check=True, capture_output=True, text=True)
            for scenario, expected in (("board_fail", "SMOKE FAIL error=1"),
                                       ("npu_fail", "SMOKE FAIL error=2"),
                                       ("inference_fail", "SMOKE FAIL error=19"),
                                       ("pass", "SMOKE PASS error=0")):
                with self.subTest(scenario=scenario):
                    result = subprocess.run([str(executable)], check=True, capture_output=True,
                                            text=True, timeout=10,
                                            env={**os.environ, "HANDGPT_TEST_SCENARIO": scenario})
                    self.assertEqual(result.stdout.count("SMOKE "), 1)
                    self.assertIn(expected, result.stdout)
                    self.assertIn("uart_timeouts=0", result.stdout)

    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler unavailable")
    def test_native_numerical_and_overflow_checks(self):
        source = r'''
#include "smoke_check.h"
#include <cassert>
#include <limits>
int main() {
    float input[16], output[16], max_error;
    for (int i=0; i<16; ++i) { input[i] = -1.0f + 2.0f*i/15.0f; output[i] = 2*input[i]; }
    assert(handgpt::check_doubled_input(input, output, 16, &max_error));
    output[0] += 0.03f;
    assert(handgpt::check_doubled_input(input, output, 16, &max_error));
    output[0] += 0.1f;
    assert(!handgpt::check_doubled_input(input, output, 16, &max_error));
    for (auto& value : output) value = 0;
    assert(!handgpt::check_doubled_input(input, output, 16, &max_error));
    output[0] = std::numeric_limits<float>::quiet_NaN();
    assert(!handgpt::check_doubled_input(input, output, 16, &max_error));
    output[0] = std::numeric_limits<float>::infinity();
    assert(!handgpt::check_doubled_input(input, output, 16, &max_error));
    assert(!handgpt::check_doubled_input(input, output, 15, &max_error));
    assert(!handgpt::check_doubled_input(nullptr, output, 16, &max_error));
    assert(handgpt::valid_range(32, 0, 32));
    assert(!handgpt::valid_range(1, std::numeric_limits<size_t>::max(), 32));
    assert(!handgpt::valid_range(std::numeric_limits<size_t>::max(), 1, 32));
    assert(handgpt::valid_sram0_buffer(0x02000000u, 4096));
    assert(handgpt::valid_sram0_buffer(0x02001000u, 32*1024));
    assert(handgpt::valid_sram0_buffer(0x023F8000u, 32*1024));
    assert(!handgpt::valid_sram0_buffer(0x200070E0u, 32*1024));
    assert(!handgpt::valid_sram0_buffer(0x02001001u, 32*1024));
    assert(!handgpt::valid_sram0_buffer(0x023FF000u, 32*1024));
    assert(!handgpt::valid_sram0_buffer(0x02400000u, 1));
    assert(!handgpt::valid_sram0_buffer(0x02000000u, 0));
    assert(!handgpt::valid_sram0_buffer(0x02000000u, std::numeric_limits<size_t>::max()));
}
'''
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "check"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-x", "c++",
                            "-I", str(smoke.APPLICATION / "include"), "-o", str(executable), "-"],
                           input=source, text=True, check=True, capture_output=True)
            subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    unittest.main()
