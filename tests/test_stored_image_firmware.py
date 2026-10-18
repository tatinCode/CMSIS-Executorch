import argparse
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tools import inference
from tools import stored_image_firmware as image
from tools.firmware_layouts import CURRENT, HP_ONLY_LARGE


class StoredImageFirmwareTests(unittest.TestCase):
    def fixture(self):
        base = HP_ONLY_LARGE.mram_base
        symbols = {"__Vectors": (base, 1984, "R"),
                   "reference_model_pte": (base + 0x10000, 3835520, "r"),
                   "reference_input": (base + 0x3C0000, 602112, "r"),
                   "reference_logits": (base + 0x460000, 4000, "r"),
                   "g_image_method_pool": (0x20001000, image.METHOD, "B"),
                   "g_smoke_status": (0x20000030, 60, "D"),
                   "__end__": (0x20050000, 0, "B"), "__HeapLimit": (0x20070000, 0, "B"),
                   "__StackLimit": (0x200F8000, 0, "B"), "__StackTop": (0x20100000, 0, "B"),
                   "__zero_table_start__": (base + 0x800, 0, "R"), "__zero_table_end__": (base + 0x810, 0, "R"),
                   "__copy_table_start__": (base + 0x820, 0, "R"), "__copy_table_end__": (base + 0x82C, 0, "R")}
        cursor = 0x02000000
        for name, size in (("g_image_output", 4000), ("g_image_input", 602112),
                           ("g_image_temporary", image.TEMPORARY), ("g_image_arena", image.ARENA)):
            symbols[name] = (cursor, size, "B")
            cursor += size
        for index, name in enumerate(("Reset_Handler", "HardFault_Handler", "SysTick_Handler", "IRQ366_Handler", "_write", "et_pal_current_ticks")):
            symbols[name] = (base + 0x1000 + index * 32, 32, "T")
        binary = bytearray(0x480000)
        struct.pack_into("<I", binary, 0, 0x20100000)
        for index, name in ((1, "Reset_Handler"), (3, "HardFault_Handler"), (15, "SysTick_Handler"), (382, "IRQ366_Handler")):
            struct.pack_into("<I", binary, index * 4, symbols[name][0] | 1)
        struct.pack_into("<4I", binary, 0x800, 0x02000000, (cursor - 0x02000000) // 4, 0x20001000, image.METHOD // 4)
        struct.pack_into("<3I", binary, 0x820, base + 0x900, 0x20000030, 15)
        return symbols, binary

    def test_valid_layout_has_reserved_tail_and_zeroed_shared_pools(self):
        symbols, binary = self.fixture()
        result = image.verify_layout(symbols, binary, 3835520)
        self.assertTrue(result["vectors_verified"])
        self.assertTrue(result["startup_zero_table_verified"])
        self.assertTrue(result["startup_copy_table_verified"])
        self.assertGreater(result["mram_headroom"], 0)
        self.assertEqual(CURRENT.limits["mram_bytes"], 2097152)

    def test_binary_cannot_enter_boot_tail(self):
        symbols, binary = self.fixture()
        binary.extend(bytes(HP_ONLY_LARGE.limits["mram_bytes"] - len(binary) + 16))
        with self.assertRaises(inference.SetupError): image.verify_layout(symbols, binary, 3835520)

    def test_scratch_in_cpu_tcm_is_rejected(self):
        symbols, binary = self.fixture()
        symbols["g_image_temporary"] = (0x200070E0, image.TEMPORARY, "B")
        with self.assertRaises(inference.SetupError): image.verify_layout(symbols, binary, 3835520)

    def test_overlapping_pools_are_rejected(self):
        symbols, binary = self.fixture()
        symbols["g_image_arena"] = (symbols["g_image_temporary"][0] + 32, image.ARENA, "B")
        with self.assertRaisesRegex(inference.SetupError, "overlap"): image.verify_layout(symbols, binary, 3835520)

    def test_bad_or_old_vectors_are_rejected(self):
        symbols, binary = self.fixture()
        struct.pack_into("<I", binary, 382 * 4, 0x80201001)
        with self.assertRaises(inference.SetupError): image.verify_layout(symbols, binary, 3835520)
        symbols, binary = self.fixture()
        symbols["__Vectors"] = (0x80200000, 1984, "R")
        with self.assertRaises(inference.SetupError): image.verify_layout(symbols, binary, 3835520)

    def test_truncated_load_image_is_rejected(self):
        symbols, binary = self.fixture()
        with self.assertRaises(inference.SetupError): image.verify_layout(symbols, binary[:0x200000], 3835520)

    def test_missing_startup_sram_zeroing_is_rejected(self):
        symbols, binary = self.fixture()
        struct.pack_into("<I", binary, 0x804, 1024)
        with self.assertRaisesRegex(inference.SetupError, "zero"): image.verify_layout(symbols, binary, 3835520)

    def test_copy_table_cannot_read_reserved_boot_tail(self):
        symbols, binary = self.fixture()
        struct.pack_into("<I", binary, 0x820, 0x80500100)
        with self.assertRaisesRegex(inference.SetupError, "copy"): image.verify_layout(symbols, binary, 3835520)

    def test_candidate_configuration_preserves_mpu_lower_bound(self):
        names = ("APP_MRAM_HE_BASE", "APP_MRAM_HE_SIZE", "APP_MRAM_HP_BASE", "APP_MRAM_HP_SIZE",
                 "APP_MRAM_USER_BASE", "APP_MRAM_USER_SIZE", "APP_HP_STACK_SIZE", "APP_HP_HEAP_SIZE")
        text = "\n".join(f"#define {name} 0x00000000" for name in names)
        text += "\n#define APP_CODE_MRAM_SIZE  (APP_MRAM_USER_BASE - APP_MRAM_HP_BASE)\n"
        result = image.candidate_memory(text)
        self.assertIn("#define APP_MRAM_HE_BASE 0x80000000", result)
        self.assertIn("#define APP_MRAM_HP_BASE 0x80001000", result)
        self.assertIn("#define APP_MRAM_USER_BASE 0x80500000", result)
        with self.assertRaises(inference.SetupError): image.candidate_memory(text.replace("APP_MRAM_HP_BASE", "MISSING"))

    def test_builder_does_not_accept_implicit_or_current_layout(self):
        with self.assertRaises(inference.SetupError):
            image.build(argparse.Namespace(layout=CURRENT.name))

    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler unavailable")
    def test_native_comparison_ranges_and_cache_line_padding(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "image_check"
            subprocess.run(["c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I", str(image.APPLICATION / "include"),
                            str(inference.ROOT / "tests/native/stored_image_check_test.cpp"),
                            "-o", str(executable)], check=True, capture_output=True, text=True)
            subprocess.run([str(executable)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
